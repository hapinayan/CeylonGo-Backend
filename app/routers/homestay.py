
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import (
    Homestay,
    HomestayAmenity,
    HomestayImage,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.homestay import (
    HomestayCreateRequest,
    HomestayListResponse,
    HomestayResponse,
    HomestayUpdateRequest,
)

from app.core.promotion_pricing import calculate_booking_price
router = APIRouter(
    prefix="/api/v1/homestays",
    tags=["Homestays"],
)


# PUBLIC: List and search active homestays
@router.get("", response_model=HomestayListResponse)
def list_homestays(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
    location: Annotated[str | None, Query(max_length=255)] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    guests: Annotated[int | None, Query(ge=1)] = None,
    name: Annotated[str | None, Query(max_length=150)] = None,
    amenity: Annotated[list[str] | None, Query()] = None,
):
    query = db.query(Homestay).filter(
        Homestay.is_active == True
    )

    if location and location.strip():
        query = query.filter(
            Homestay.location.ilike(f"%{location.strip()}%")
        )

    if min_price is not None:
        query = query.filter(
            Homestay.price_per_night >= min_price
        )

    if max_price is not None:
        query = query.filter(
            Homestay.price_per_night <= max_price
        )

    if min_price is not None and max_price is not None:
        if min_price > max_price:
            raise HTTPException(
                status_code=422,
                detail="Minimum price cannot exceed maximum price",
            )

    if guests is not None:
        query = query.filter(
            Homestay.max_guests >= guests
        )

    if name and name.strip():
        query = query.filter(
            Homestay.name.ilike(f"%{name.strip()}%")
        )

    if amenity:
        for item in amenity:
            cleaned = item.strip()

            if not cleaned or len(cleaned) > 100:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid amenity",
                )

            query = query.filter(
                Homestay.amenities.any(
                    func.lower(HomestayAmenity.name)
                    == cleaned.lower()
                )
            )

    total = query.count()

    homestays = (
        query.options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .order_by(Homestay.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return HomestayListResponse(
        items=homestays,
        total=total,
        page=page,
        page_size=page_size,
    )


# OWNER: List own active and inactive homestays
# IMPORTANT: This route must appear before /{homestay_id}
@router.get(
    "/mine",
    response_model=HomestayListResponse,
)
def list_my_homestays(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if profile is not None:
        query = db.query(Homestay).filter(
            (Homestay.provider_profile_id == profile.id)
            | (
                (Homestay.provider_profile_id == None)
                & (Homestay.owner_id == current_user.id)
            )
        )
    else:
        query = db.query(Homestay).filter(
            Homestay.owner_id == current_user.id
        )

    total = query.count()

    homestays = (
        query.options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .order_by(Homestay.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return HomestayListResponse(
        items=homestays,
        total=total,
        page=page,
        page_size=page_size,
    )


# PUBLIC: Get active homestay details
@router.get("/{homestay_id}", response_model=HomestayResponse)
def get_homestay(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    homestay = (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    return homestay


# PUBLIC: Check homestay availability
@router.get("/{homestay_id}/availability")
def check_homestay_availability(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    check_in: date = Query(...),
    check_out: date = Query(...),
):
    if check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    if check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="Check-out date must be after check-in date",
        )

    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == homestay_id,
            Booking.status == "confirmed",
            Booking.check_in < check_out,
            Booking.check_out > check_in,
        )
        .first()
    )

    return {
        "homestay_id": homestay_id,
        "check_in": check_in,
        "check_out": check_out,
        "available": overlapping_booking is None,
    }

# PUBLIC: Preview the final booking price
@router.get("/{homestay_id}/price-preview")
def preview_homestay_price(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    check_in: date = Query(...),
    check_out: date = Query(...),
):
    if check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    if check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="Check-out date must be after check-in date",
        )

    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )

    if homestay is None:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    nights = (check_out - check_in).days

    original_price = homestay.price_per_night * nights

    final_price = calculate_booking_price(
        db=db,
        homestay_id=homestay.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=homestay.price_per_night,
    )

    discount_amount = original_price - final_price

    return {
        "homestay_id": homestay.id,
        "check_in": check_in,
        "check_out": check_out,
        "nights": nights,
        "price_per_night": homestay.price_per_night,
        "original_price": original_price,
        "discount_amount": discount_amount,
        "final_price": final_price,
    }


# OWNER / ADMIN: Create homestay
@router.post(
    "",
    response_model=HomestayResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_homestay(
    request: HomestayCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    provider_profile_id = None
    if current_user.role == "provider":
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
            if profile is not None:
                provider_profile_id = profile.id
        except Exception:
            provider_profile_id = None

    homestay = Homestay(
        owner_id=current_user.id,
        provider_profile_id=provider_profile_id,
        name=request.name,
        description=request.description,
        location=request.location,
        price_per_night=request.price_per_night,
        max_guests=request.max_guests,
        is_active=True,
    )

    homestay.images = [
        HomestayImage(
            image_url=image.image_url,
            is_primary=image.is_primary,
        )
        for image in request.images
    ]

    homestay.amenities = [
        HomestayAmenity(name=amenity)
        for amenity in request.amenities
    ]

    try:
        db.add(homestay)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(homestay)

    return (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay.id)
        .first()
    )


# OWNER / ADMIN: Update or deactivate homestay
@router.put(
    "/{homestay_id}",
    response_model=HomestayResponse,
)
def update_homestay(
    homestay_id: int,
    request: HomestayUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    homestay = (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay_id)
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    profile = None
    if current_user.role != "admin" and (
        homestay.provider_profile_id is not None
        or current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=403,
            detail="You cannot manage this homestay",
        )

    # Automatically associate legacy homestay with provider profile upon update
    if (
        homestay.provider_profile_id is None
        and profile is not None
        and homestay.owner_id == current_user.id
    ):
        homestay.provider_profile_id = profile.id

    # Prevent deactivation if active or upcoming confirmed bookings exist
    if not request.is_active and homestay.is_active:
        active_bookings = (
            db.query(Booking)
            .filter(
                Booking.homestay_id == homestay_id,
                Booking.status == "confirmed",
                Booking.check_out >= date.today(),
            )
            .count()
        )
        if active_bookings > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot deactivate homestay with active or upcoming confirmed bookings",
            )

    homestay.name = request.name
    homestay.description = request.description
    homestay.location = request.location
    homestay.price_per_night = request.price_per_night
    homestay.max_guests = request.max_guests
    homestay.is_active = request.is_active

    # Safe synchronization of images (preserves existing when omitted)
    if request.images is not None:
        existing_images_by_url = {img.image_url.strip(): img for img in homestay.images}
        req_images_by_url = {img.image_url.strip(): img for img in request.images}

        # Remove images no longer requested
        for img_url, img_obj in list(existing_images_by_url.items()):
            if img_url not in req_images_by_url:
                homestay.images.remove(img_obj)
                db.delete(img_obj)

        # Update or add requested images
        for img_input in request.images:
            clean_url = img_input.image_url.strip()
            if clean_url in existing_images_by_url:
                existing_images_by_url[clean_url].is_primary = img_input.is_primary
            else:
                homestay.images.append(
                    HomestayImage(
                        image_url=clean_url,
                        is_primary=img_input.is_primary,
                    )
                )

    # Safe synchronization of amenities (avoids UQ_homestay_amenities_name constraint collision)
    if request.amenities is not None:
        existing_amenities_by_lower = {
            a.name.strip().lower(): a for a in homestay.amenities
        }

        unique_req_amenities = {}
        for a_name in request.amenities:
            clean_name = a_name.strip()
            if clean_name:
                key = clean_name.lower()
                if key not in unique_req_amenities:
                    unique_req_amenities[key] = clean_name

        req_keys = set(unique_req_amenities.keys())

        # Remove amenities no longer requested
        for key, a_obj in list(existing_amenities_by_lower.items()):
            if key not in req_keys:
                homestay.amenities.remove(a_obj)
                db.delete(a_obj)

        # Insert only genuinely new amenities; preserve existing ones in-place
        for key, orig_name in unique_req_amenities.items():
            if key not in existing_amenities_by_lower:
                homestay.amenities.append(HomestayAmenity(name=orig_name))

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay_id)
        .first()
    )


# OWNER / ADMIN: Soft delete (deactivate) homestay
@router.delete(
    "/{homestay_id}",
    summary="Soft delete (deactivate) a homestay listing",
)
def delete_homestay(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    homestay = db.query(Homestay).filter(Homestay.id == homestay_id).first()
    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    profile = None
    if current_user.role != "admin" and (
        homestay.provider_profile_id is not None
        or current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=403,
            detail="You cannot manage this homestay",
        )

    # Prevent deactivation if active or upcoming confirmed bookings exist
    active_bookings = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == homestay_id,
            Booking.status == "confirmed",
            Booking.check_out >= date.today(),
        )
        .count()
    )
    if active_bookings > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate homestay with active or upcoming confirmed bookings",
        )

    homestay.is_active = False
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Homestay deactivated successfully",
        "id": homestay_id,
        "is_active": False,
    }
