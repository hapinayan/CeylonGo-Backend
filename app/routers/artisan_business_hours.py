from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.artisan import ArtisanBusinessHours
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.artisan_images import get_optional_user
from app.schemas.artisan_business_hours import (
    ArtisanBusinessHoursBase,
    ArtisanBusinessHoursCreate,
    ArtisanBusinessHoursResponse,
    ArtisanBusinessHoursUpdate,
    ArtisanWeeklyBusinessHoursRequest,
)

router = APIRouter(prefix="/api/v1/artisan-listings", tags=["Artisan Business Hours"])


def _verify_listing_ownership(
    listing_id: int,
    provider_profile: ProviderProfile,
    db: Session,
) -> ArtisanListing:
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can manage business hours",
        )

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )
    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this listing",
        )
    return listing


# ==============================================================================
# Public / Owner Retrieval Endpoints
# ==============================================================================

@router.get(
    "/{listing_id}/business-hours",
    response_model=list[ArtisanBusinessHoursResponse],
    summary="Get weekly opening hours for an artisan listing",
)
@router.get(
    "/{listing_id}/opening-hours",
    response_model=list[ArtisanBusinessHoursResponse],
    include_in_schema=False,
)
def get_artisan_business_hours(
    listing_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_optional_user)] = None,
):
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    is_owner = False
    if current_user and current_user.role == "provider":
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == current_user.id)
            .first()
        )
        if profile and profile.id == listing.provider_profile_id:
            is_owner = True

    if not is_owner:
        provider = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.id == listing.provider_profile_id)
            .first()
        )
        if not (
            listing.is_active
            and listing.approval_status == "approved"
            and provider
            and provider.approval_status == "approved"
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artisan listing not found",
            )

    hours = (
        db.query(ArtisanBusinessHours)
        .filter(ArtisanBusinessHours.artisan_listing_id == listing_id)
        .order_by(ArtisanBusinessHours.day_of_week.asc())
        .all()
    )
    return [ArtisanBusinessHoursResponse.model_validate(h) for h in hours]


# ==============================================================================
# Provider Management Endpoints
# ==============================================================================

@router.post(
    "/{listing_id}/business-hours",
    response_model=ArtisanBusinessHoursResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add opening hours for a specific weekday",
)
def create_artisan_business_hours(
    listing_id: int,
    payload: ArtisanBusinessHoursCreate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    listing = _verify_listing_ownership(listing_id, provider_profile, db)

    # Check for duplicate day
    existing = (
        db.query(ArtisanBusinessHours)
        .filter(
            ArtisanBusinessHours.artisan_listing_id == listing_id,
            ArtisanBusinessHours.day_of_week == payload.day_of_week,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Business hours for day_of_week {payload.day_of_week} already exist. Use PUT or PATCH to update.",
        )

    new_hours = ArtisanBusinessHours(
        artisan_listing_id=listing.id,
        day_of_week=payload.day_of_week,
        open_time=payload.open_time if not payload.is_closed else None,
        close_time=payload.close_time if not payload.is_closed else None,
        is_closed=payload.is_closed,
    )
    db.add(new_hours)
    listing.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(new_hours)
    return ArtisanBusinessHoursResponse.model_validate(new_hours)


@router.put(
    "/{listing_id}/business-hours",
    response_model=list[ArtisanBusinessHoursResponse],
    summary="Set or replace entire weekly business hours schedule",
)
def set_weekly_business_hours(
    listing_id: int,
    payload: ArtisanWeeklyBusinessHoursRequest,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    listing = _verify_listing_ownership(listing_id, provider_profile, db)

    # Validate no duplicate weekdays in payload
    days_seen = set()
    for item in payload.hours:
        if item.day_of_week in days_seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate day_of_week {item.day_of_week} in weekly schedule payload",
            )
        days_seen.add(item.day_of_week)

    # Replace existing hours in transaction
    db.query(ArtisanBusinessHours).filter(
        ArtisanBusinessHours.artisan_listing_id == listing_id
    ).delete()

    created_records = []
    for item in sorted(payload.hours, key=lambda x: x.day_of_week):
        rec = ArtisanBusinessHours(
            artisan_listing_id=listing_id,
            day_of_week=item.day_of_week,
            open_time=item.open_time if not item.is_closed else None,
            close_time=item.close_time if not item.is_closed else None,
            is_closed=item.is_closed,
        )
        db.add(rec)
        created_records.append(rec)

    listing.updated_at = datetime.now(timezone.utc)
    db.commit()

    for rec in created_records:
        db.refresh(rec)

    return [ArtisanBusinessHoursResponse.model_validate(r) for r in created_records]


@router.put(
    "/{listing_id}/business-hours/{day_of_week}",
    response_model=ArtisanBusinessHoursResponse,
    summary="Update or set opening hours for a specific weekday",
)
def update_or_set_day_schedule(
    listing_id: int,
    day_of_week: int,
    payload: ArtisanBusinessHoursBase,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="day_of_week must be between 0 (Sunday) and 6 (Saturday)",
        )

    listing = _verify_listing_ownership(listing_id, provider_profile, db)

    schedule = (
        db.query(ArtisanBusinessHours)
        .filter(
            ArtisanBusinessHours.artisan_listing_id == listing_id,
            ArtisanBusinessHours.day_of_week == day_of_week,
        )
        .first()
    )

    if schedule:
        schedule.open_time = payload.open_time if not payload.is_closed else None
        schedule.close_time = payload.close_time if not payload.is_closed else None
        schedule.is_closed = payload.is_closed
    else:
        schedule = ArtisanBusinessHours(
            artisan_listing_id=listing_id,
            day_of_week=day_of_week,
            open_time=payload.open_time if not payload.is_closed else None,
            close_time=payload.close_time if not payload.is_closed else None,
            is_closed=payload.is_closed,
        )
        db.add(schedule)

    listing.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(schedule)
    return ArtisanBusinessHoursResponse.model_validate(schedule)


@router.patch(
    "/{listing_id}/business-hours/{day_of_week}",
    response_model=ArtisanBusinessHoursResponse,
    summary="Partially update opening hours for a specific weekday",
)
def patch_day_schedule(
    listing_id: int,
    day_of_week: int,
    payload: ArtisanBusinessHoursUpdate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="day_of_week must be between 0 (Sunday) and 6 (Saturday)",
        )

    listing = _verify_listing_ownership(listing_id, provider_profile, db)

    schedule = (
        db.query(ArtisanBusinessHours)
        .filter(
            ArtisanBusinessHours.artisan_listing_id == listing_id,
            ArtisanBusinessHours.day_of_week == day_of_week,
        )
        .first()
    )
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business hours for day_of_week {day_of_week} not found",
        )

    if payload.is_closed is not None:
        schedule.is_closed = payload.is_closed
    if payload.open_time is not None:
        schedule.open_time = payload.open_time
    if payload.close_time is not None:
        schedule.close_time = payload.close_time

    # Validate resulting state
    if not schedule.is_closed:
        if schedule.open_time is None or schedule.close_time is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="open_time and close_time must be set when is_closed is False",
            )
        if schedule.close_time <= schedule.open_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"close_time ({schedule.close_time}) must be after open_time ({schedule.open_time})",
            )
    else:
        schedule.open_time = None
        schedule.close_time = None

    listing.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(schedule)
    return ArtisanBusinessHoursResponse.model_validate(schedule)


@router.delete(
    "/{listing_id}/business-hours/{day_of_week}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete opening hours for a specific weekday",
)
def delete_artisan_business_hours(
    listing_id: int,
    day_of_week: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="day_of_week must be between 0 (Sunday) and 6 (Saturday)",
        )

    listing = _verify_listing_ownership(listing_id, provider_profile, db)

    schedule = (
        db.query(ArtisanBusinessHours)
        .filter(
            ArtisanBusinessHours.artisan_listing_id == listing_id,
            ArtisanBusinessHours.day_of_week == day_of_week,
        )
        .first()
    )
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business hours for day_of_week {day_of_week} not found",
        )

    db.delete(schedule)
    listing.updated_at = datetime.now(timezone.utc)
    db.commit()
    return None

