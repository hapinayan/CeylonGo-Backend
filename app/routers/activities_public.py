"""
FastAPI Router for Module 03 — Public Activity Discovery, Search, Categories, Details & Photo Gallery.

Requirements covered:
- A01: Activity Discovery (browse approved, published experiences)
- A02: Search & Filters (location, category, price, duration, keyword)
- A03: Activity Categories (cultural, adventure, local, community taxonomy)
- A04: Activity Details (itinerary, requirements, duration, provider info)
- A05: Photo Gallery (public activity images)
- A06: Location & Maps (meeting point and coordinates)
- A15: Contact Provider (public organizer contact channels)
- D02: Safety Instructions (safety rules and equipment retrieval)
- D03: Meeting Point (public retrieval)
"""

from decimal import Decimal
import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.publication import public_activity
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.schemas.activity import (
    APPROVED_ACTIVITY_CATEGORIES,
    ActivityCategory,
    ActivityCategoryDetail,
    ActivityImageResponse,
    ActivityOrganizerContact,
    ActivityPagination,
    ActivityPublicDetail,
    ActivityPublicSummary,
    make_short_description,
)


router = APIRouter(
    prefix="/api/v1/activities",
    tags=["Activities - Public"],
)


def _resolve_cover_image_url(images: list[ActivityImage]) -> str | None:
    """Find the designated cover image or fallback to the lowest display_order."""
    if not images:
        return None
    for img in images:
        if img.is_cover:
            return img.image_url
    sorted_imgs = sorted(images, key=lambda x: (x.display_order, x.id))
    return sorted_imgs[0].image_url


# ==============================================================================
# 1. Public Activity Discovery & Search (A01, A02)
# ==============================================================================


@router.get(
    "",
    response_model=ActivityPagination,
    summary="Browse approved, published activities",
)
def list_activities(
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(
        default=None,
        max_length=100,
        description="Search keyword in title, description, or city",
    ),
    category: ActivityCategory | None = Query(
        default=None,
        description="Filter by approved activity category",
    ),
    district: str | None = Query(
        default=None,
        max_length=100,
        description="Filter by Sri Lankan district",
    ),
    city: str | None = Query(
        default=None,
        max_length=100,
        description="Filter by city",
    ),
    min_price: Decimal | None = Query(
        default=None,
        ge=Decimal("0"),
        description="Minimum price in LKR",
    ),
    max_price: Decimal | None = Query(
        default=None,
        ge=Decimal("0"),
        description="Maximum price in LKR",
    ),
    min_duration: int | None = Query(
        default=None,
        gt=0,
        description="Minimum duration in minutes",
    ),
    max_duration: int | None = Query(
        default=None,
        gt=0,
        description="Maximum duration in minutes",
    ),
    sort_by: Literal[
        "newest",
        "price_asc",
        "price_desc",
        "duration_asc",
        "duration_desc",
    ] = Query(
        default="newest",
        description="Deterministic sorting criteria",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=12,
        ge=1,
        le=100,
        description="Items per page",
    ),
):
    """
    Public discovery endpoint for activities.
    Strictly returns approved, active activities from approved providers.
    """
    # Parameter range validations
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_price cannot exceed max_price",
        )

    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_duration cannot exceed max_duration",
        )

    # Base publication filter
    conditions = [public_activity()]

    # Keyword search across title, description, and city
    if search:
        search_pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                Activity.title.ilike(search_pattern),
                Activity.description.ilike(search_pattern),
                Activity.city.ilike(search_pattern),
            )
        )

    # Category filter
    if category:
        conditions.append(Activity.category == category)

    # Location filters
    if district:
        conditions.append(Activity.district.ilike(f"%{district.strip()}%"))

    if city:
        conditions.append(Activity.city.ilike(f"%{city.strip()}%"))

    # Price bounds
    if min_price is not None:
        conditions.append(Activity.price >= min_price)

    if max_price is not None:
        conditions.append(Activity.price <= max_price)

    # Duration bounds
    if min_duration is not None:
        conditions.append(Activity.duration_minutes >= min_duration)

    if max_duration is not None:
        conditions.append(Activity.duration_minutes <= max_duration)

    # Total count query
    total = db.query(func.count(Activity.id)).filter(*conditions).scalar() or 0
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    # Deterministic sorting
    if sort_by == "price_asc":
        order_clauses = [Activity.price.asc(), Activity.id.asc()]
    elif sort_by == "price_desc":
        order_clauses = [Activity.price.desc(), Activity.id.asc()]
    elif sort_by == "duration_asc":
        order_clauses = [Activity.duration_minutes.asc(), Activity.id.asc()]
    elif sort_by == "duration_desc":
        order_clauses = [Activity.duration_minutes.desc(), Activity.id.asc()]
    else:  # "newest"
        order_clauses = [Activity.created_at.desc(), Activity.id.desc()]

    # Fetch paginated results with eager loading to prevent N+1 queries
    offset = (page - 1) * page_size
    activities = (
        db.query(Activity)
        .options(
            selectinload(Activity.images),
            joinedload(Activity.provider_profile),
        )
        .filter(*conditions)
        .order_by(*order_clauses)
        .offset(offset)
        .limit(page_size)
        .all()
    )

    items = [
        ActivityPublicSummary(
            id=act.id,
            title=act.title,
            category=act.category,
            district=act.district,
            city=act.city,
            duration_minutes=act.duration_minutes,
            pricing_type=act.pricing_type,
            price=act.price,
            currency=act.currency,
            minimum_age=act.minimum_age,
            cover_image_url=_resolve_cover_image_url(act.images),
            short_description=make_short_description(act.description),
            provider_business_name=(
                act.provider_profile.business_name if act.provider_profile else None
            ),
        )
        for act in activities
    ]

    return ActivityPagination(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ==============================================================================
# 2. Activity Category Taxonomy (A03)
# ==============================================================================


@router.get(
    "/categories",
    response_model=list[ActivityCategoryDetail],
    summary="List approved activity categories",
)
def list_activity_categories():
    """
    Returns the official 4 approved activity categories:
    cultural, adventure, local, and community.
    """
    return APPROVED_ACTIVITY_CATEGORIES


# ==============================================================================
# 3. Public Activity Details (A04, A06, A15, D02, D03)
# ==============================================================================


@router.get(
    "/{activity_id}",
    response_model=ActivityPublicDetail,
    summary="Get public activity details",
)
def get_public_activity_detail(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns detailed view for a single approved, published activity.
    Includes itinerary, safety rules, required equipment, meeting point coordinates,
    and public organizer contact information.
    Returns HTTP 404 if activity is not approved or published.
    """
    activity = (
        db.query(Activity)
        .options(
            selectinload(Activity.images),
            joinedload(Activity.provider_profile),
        )
        .filter(
            Activity.id == activity_id,
            public_activity(),
        )
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    # Sort images by display_order ASC, then id ASC
    sorted_images = (
        sorted(activity.images, key=lambda x: (x.display_order, x.id))
        if activity.images
        else []
    )

    # Expose only safe public organizer contact info
    organizer = None
    if activity.provider_profile:
        organizer = ActivityOrganizerContact(
            business_name=activity.provider_profile.business_name,
            phone=activity.provider_profile.phone,
            secondary_phone=activity.provider_profile.secondary_phone,
            description=activity.provider_profile.description,
        )

    return ActivityPublicDetail(
        id=activity.id,
        title=activity.title,
        description=activity.description,
        category=activity.category,
        district=activity.district,
        city=activity.city,
        duration_minutes=activity.duration_minutes,
        itinerary=activity.itinerary,
        requirements=activity.requirements,
        safety_rules=activity.safety_rules,
        required_equipment=activity.required_equipment,
        minimum_age=activity.minimum_age,
        pricing_type=activity.pricing_type,
        price=activity.price,
        currency=activity.currency,
        meeting_point=activity.meeting_point,
        latitude=activity.latitude,
        longitude=activity.longitude,
        organizer=organizer,
        images=[
            ActivityImageResponse(
                id=img.id,
                activity_id=img.activity_id,
                image_url=img.image_url,
                alt_text=img.alt_text,
                display_order=img.display_order,
                is_cover=img.is_cover,
            )
            for img in sorted_images
        ],
        created_at=activity.created_at,
    )


# ==============================================================================
# 4. Public Photo Gallery (A05)
# ==============================================================================


@router.get(
    "/{activity_id}/images",
    response_model=list[ActivityImageResponse],
    summary="Get public photo gallery for an activity",
)
def get_public_activity_images(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns all photos for an approved, published activity, sorted by display_order.
    Returns HTTP 404 if parent activity is unpublished or does not exist.
    """
    activity = (
        db.query(Activity.id)
        .filter(
            Activity.id == activity_id,
            public_activity(),
        )
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    images = (
        db.query(ActivityImage)
        .filter(ActivityImage.activity_id == activity_id)
        .order_by(ActivityImage.display_order.asc(), ActivityImage.id.asc())
        .all()
    )

    return images
