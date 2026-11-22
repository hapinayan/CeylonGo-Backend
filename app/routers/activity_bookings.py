
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, require_approved_provider
from app.core.publication import public_activity, public_eco
from app.database.connection import get_db

from app.models.activity import Activity
from app.models.eco_experience import EcoExperience
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.user import User

from app.schemas.activity_booking import (
    ActivityBookingCancellation,
    ActivityBookingCreate,
    ActivityBookingProviderItem,
    ActivityBookingProviderListResponse,
    ActivityBookingResponse,
    ActivityCustomerSummary,
    ActivitySlotSummary,
)

from app.services.activity_capacity import (
    reserve_activity_capacity,
    release_activity_capacity,
)


from app.services.provider_reservations import record_native_event

router = APIRouter(
    prefix="/api/v1/activity-bookings",
    tags=["Activity Bookings"],
)


def require_activity_provider(
    profile: Annotated[
        ProviderProfile,
        Depends(require_approved_provider),
    ],
) -> ProviderProfile:
    """Allow only approved activity providers."""
    if profile.provider_type != "activity":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity provider access required",
        )
    return profile


# ==========================================================
# CREATE ACTIVITY BOOKING
# ==========================================================

@router.post(
    "",
    response_model=ActivityBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_activity_booking(
    payload: ActivityBookingCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):

    # Only travelers can book activities.
    if current_user.role != "traveler":
        raise HTTPException(
            status_code=403,
            detail="Only travelers can create activity bookings",
        )

    # Online payment is not enabled yet.
    if payload.payment_method != "pay_at_location":
        raise HTTPException(
            status_code=422,
            detail="Only pay-at-location is currently supported",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Find an active future slot.
    slot = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.id == payload.activity_slot_id,
            ActivitySlot.is_active == True,
            ActivitySlot.starts_at > now,
        )
        .first()
    )

    if slot is None:
        raise HTTPException(
            status_code=404,
            detail="Active future activity slot not found",
        )

    # Only approved activities from approved providers.
    activity = (
        db.query(Activity)
        .with_hint(Activity, "WITH (HOLDLOCK)", "mssql")
        .join(
            ProviderProfile,
            Activity.provider_profile_id == ProviderProfile.id,
        )
        .filter(
            Activity.id == slot.activity_id,
            public_activity(hold=True),
            ProviderProfile.approval_status == "approved",
            ProviderProfile.provider_type == "activity",
        )
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=404,
            detail="Approved activity not found",
        )

    if slot.eco_experience_id is not None and not (
        db.query(EcoExperience.id)
        .with_hint(EcoExperience, "WITH (HOLDLOCK)", "mssql")
        .filter(EcoExperience.id == slot.eco_experience_id, public_eco(hold=True))
        .first()
    ):
        raise HTTPException(404, detail="Approved Eco-Tourism experience not found")

    if payload.participants > slot.capacity:
        raise HTTPException(
            status_code=409,
            detail="Participants exceed slot capacity",
        )

    # Calculate price on the server.
    unit_price = Decimal(str(activity.price))

    if activity.pricing_type == "per_person":
        total_price = unit_price * payload.participants

    elif activity.pricing_type == "per_group":
        total_price = unit_price

    else:
        raise HTTPException(
            status_code=409,
            detail="Unsupported activity pricing type",
        )

    # Reserve capacity and insert booking
    # in the same database transaction.
    try:
        reserved = reserve_activity_capacity(
            db=db,
            slot_id=slot.id,
            participants=payload.participants,
        )

        if not reserved:
            raise HTTPException(
                status_code=409,
                detail="Not enough available places",
            )

        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=current_user.id,
            participants=payload.participants,
            total_price=total_price,
            currency=activity.currency,
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
        )

        db.add(booking)
        db.flush()
        provider = db.get(ProviderProfile, activity.provider_profile_id)
        record_native_event(
            db, booking, "activity", provider.id, None, "confirmed",
            current_user.id, provider.user_id,
        )
        db.commit()
        db.refresh(booking)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    return booking

# ==========================================================
# GET MY ACTIVITY BOOKINGS
# ==========================================================

@router.get(
    "/me",
    response_model=list[ActivityBookingResponse],
    summary="Get my activity bookings",
)
def get_my_activity_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if current_user.role != "traveler":
        raise HTTPException(
            status_code=403,
            detail="Only travelers can view their activity bookings",
        )

    bookings = (
        db.query(ActivityBooking)
        .filter(ActivityBooking.user_id == current_user.id)
        .order_by(ActivityBooking.created_at.desc())
        .all()
    )

    return bookings


# ==========================================================
# PROVIDER BOOKINGS WORKSPACE
# ==========================================================

@router.get(
    "/provider",
    response_model=ActivityBookingProviderListResponse,
    summary="Provider view of activity bookings",
)
def get_provider_activity_bookings(
    provider_profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    activity_id: Annotated[int | None, Query()] = None,
    slot_id: Annotated[int | None, Query()] = None,
):
    """
    Provider-scoped activity bookings list.
    Enforces that providers view only bookings for activities they own.
    """
    query = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(Activity.provider_profile_id == provider_profile.id)
    )

    if activity_id is not None:
        target_activity = (
            db.query(Activity)
            .filter(
                Activity.id == activity_id,
                Activity.provider_profile_id == provider_profile.id,
            )
            .first()
        )
        if not target_activity:
            raise HTTPException(
                status_code=404,
                detail="Activity not found or not owned by you",
            )
        query = query.filter(Activity.id == activity_id)

    if slot_id is not None:
        query = query.filter(ActivityBooking.activity_slot_id == slot_id)

    if status and status.strip():
        clean_status = status.strip().lower()
        query = query.filter(ActivityBooking.booking_status == clean_status)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    bookings = (
        query.order_by(ActivityBooking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for b in bookings:
        slot = b.activity_slot
        activity = slot.activity if slot else None
        customer = None
        if b.traveler:
            customer = ActivityCustomerSummary(
                id=b.traveler.id,
                full_name=b.traveler.full_name,
                email=b.traveler.email,
            )

        slot_summary = None
        if slot:
            slot_summary = ActivitySlotSummary(
                id=slot.id,
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                capacity=slot.capacity,
                reserved_count=slot.reserved_count,
            )

        items.append(
            ActivityBookingProviderItem(
                id=b.id,
                activity_id=activity.id if activity else 0,
                activity_title=activity.title if activity else "Activity",
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
                slot=slot_summary,
                customer=customer,
            )
        )

    return ActivityBookingProviderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/provider/{booking_id}",
    response_model=ActivityBookingProviderItem,
    summary="Provider view of single activity booking",
)
def get_provider_activity_booking_detail(
    booking_id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """Get single booking details with activity provider ownership verification."""
    booking = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(
            ActivityBooking.id == booking_id,
            Activity.provider_profile_id == provider_profile.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Activity booking not found or not owned by you",
        )

    slot = booking.activity_slot
    activity = slot.activity if slot else None
    customer = None
    if booking.traveler:
        customer = ActivityCustomerSummary(
            id=booking.traveler.id,
            full_name=booking.traveler.full_name,
            email=booking.traveler.email,
        )

    slot_summary = None
    if slot:
        slot_summary = ActivitySlotSummary(
            id=slot.id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            capacity=slot.capacity,
            reserved_count=slot.reserved_count,
        )

    return ActivityBookingProviderItem(
        id=booking.id,
        activity_id=activity.id if activity else 0,
        activity_title=activity.title if activity else "Activity",
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
        slot=slot_summary,
        customer=customer,
    )


# ==========================================================
# CANCEL ACTIVITY BOOKING
# ==========================================================

@router.post(
    "/{booking_id}/cancel",
    response_model=ActivityBookingResponse,
    summary="Cancel an activity booking",
)
def cancel_activity_booking(
    booking_id: int,
    payload: ActivityBookingCancellation,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):

    if current_user.role != "traveler":
        raise HTTPException(
            status_code=403,
            detail="Only travelers can cancel their bookings",
        )

    # Travelers can only cancel their own bookings.
    booking = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.id == booking_id,
            ActivityBooking.user_id == current_user.id,
        )
        .first()
    )

    if booking is None:
        raise HTTPException(
            status_code=404,
            detail="Booking not found",
        )

    if booking.booking_status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Booking is already cancelled",
        )

    if booking.booking_status not in ("pending", "confirmed"):
        raise HTTPException(
            status_code=409,
            detail="This booking cannot be cancelled",
        )

    # Only unpaid pay-at-location bookings
    # are eligible under the temporary policy.
    if (
        booking.payment_method != "pay_at_location"
        or booking.payment_status != "unpaid"
    ):
        raise HTTPException(
            status_code=409,
            detail="This booking requires a separate cancellation policy",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    slot = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.id == booking.activity_slot_id,
        )
        .first()
    )

    if slot is None:
        raise HTTPException(
            status_code=409,
            detail="Booking slot not found",
        )

    # PROVISIONAL RULE:
    # Cancellation at least 24 hours before start.
    # Must be approved before production use.
    if slot.starts_at <= now + timedelta(hours=24):
        raise HTTPException(
            status_code=409,
            detail="Cancellation cutoff has passed",
        )

    try:
        # Conditional update prevents a booking
        # from being cancelled twice.
        statement = (
            update(ActivityBooking)
            .where(
                ActivityBooking.id == booking_id,
                ActivityBooking.user_id == current_user.id,
                ActivityBooking.booking_status.in_(
                    ["pending", "confirmed"]
                ),
                ActivityBooking.payment_method == "pay_at_location",
                ActivityBooking.payment_status == "unpaid",
            )
            .values(
                booking_status="cancelled",
                cancellation_reason=payload.reason,
                cancelled_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )

        result = db.execute(statement)

        if result.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Booking already cancelled or no longer eligible",
            )

        # Release reserved places in the same transaction.
        released = release_activity_capacity(
            db=db,
            slot_id=booking.activity_slot_id,
            participants=booking.participants,
        )

        if not released:
            raise HTTPException(
                status_code=409,
                detail="Unable to release reserved capacity",
            )

        activity = db.get(Activity, slot.activity_id)
        provider = db.get(ProviderProfile, activity.provider_profile_id)
        record_native_event(
            db, booking, "activity", provider.id, booking.booking_status,
            "cancelled", current_user.id, provider.user_id,
        )
        db.commit()
        db.expire_all()

        updated_booking = (
            db.query(ActivityBooking)
            .filter(ActivityBooking.id == booking_id)
            .first()
        )

        return updated_booking

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise


# ==========================================================
# PROVIDER BOOKING LIFECYCLE MANAGEMENT
# ==========================================================

@router.post(
    "/{booking_id}/confirm",
    response_model=ActivityBookingResponse,
    summary="Provider confirms pending activity booking",
)
def confirm_activity_booking(
    booking_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    booking = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(
            ActivityBooking.id == booking_id,
            Activity.provider_profile_id == provider_profile.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Activity booking not found or not owned by you",
        )

    if booking.booking_status == "confirmed":
        raise HTTPException(
            status_code=409,
            detail="Booking is already confirmed",
        )

    if booking.booking_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot confirm a booking with status '{booking.booking_status}'",
        )

    booking.booking_status = "confirmed"
    booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


@router.post(
    "/{booking_id}/reject",
    response_model=ActivityBookingResponse,
    summary="Provider rejects pending activity booking",
)
def reject_activity_booking(
    booking_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    booking = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(
            ActivityBooking.id == booking_id,
            Activity.provider_profile_id == provider_profile.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Activity booking not found or not owned by you",
        )

    if booking.booking_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject a booking with status '{booking.booking_status}'",
        )

    booking.booking_status = "rejected"
    booking.cancellation_reason = "Booking rejected by activity provider"
    booking.cancelled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        release_activity_capacity(
            db=db,
            slot_id=booking.activity_slot_id,
            participants=booking.participants,
        )
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


@router.post(
    "/{booking_id}/complete",
    response_model=ActivityBookingResponse,
    summary="Provider marks confirmed activity booking as completed",
)
def complete_activity_booking(
    booking_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    booking = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(
            ActivityBooking.id == booking_id,
            Activity.provider_profile_id == provider_profile.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Activity booking not found or not owned by you",
        )

    if booking.booking_status == "completed":
        raise HTTPException(
            status_code=409,
            detail="Booking is already marked as completed",
        )

    if booking.booking_status != "confirmed":
        raise HTTPException(
            status_code=400,
            detail=f"Only confirmed bookings can be completed (current status: '{booking.booking_status}')",
        )

    booking.booking_status = "completed"
    booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking
