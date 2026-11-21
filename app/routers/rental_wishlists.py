"""
FastAPI Router for CeylonGo Module 09 — Rental Vehicle Wishlists / Favorites (TR-16).

Requirements covered:
- TR-16: Wishlist / Favorites (Save/unsave listings to traveler's personal wishlist;
  list saved rental vehicles with pagination, images, and real-time availability).
"""

from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.rental import RentalLocation
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import ListingReference, Wishlist
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.rental_reviews import sync_rental_listing_reference

router = APIRouter(
    tags=["Rental Wishlists"],
)


# ==============================================================================
# SCHEMAS
# ==============================================================================

class WishlistActionResponse(BaseModel):
    message: str
    vehicle_id: int
    saved: bool
    wishlist_id: int | None = None


class WishlistStatusResponse(BaseModel):
    vehicle_id: int
    saved: bool


class SavedRentalVehicleItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    wishlist_id: int
    saved_at: datetime
    vehicle_id: int
    vehicle_name: str
    vehicle_type: str
    price_per_day: Decimal
    currency: str = "LKR"
    status: str
    district: str | None = None
    city: str | None = None
    location_name: str | None = None
    primary_image_url: str | None = None
    is_available: bool = True


class RentalWishlistPagination(BaseModel):
    items: list[SavedRentalVehicleItem]
    total: int
    page: int
    limit: int
    pages: int


# ==============================================================================
# TR-16: ADD TO WISHLIST
# ==============================================================================

@router.post(
    "/api/v1/rental-wishlist/{vehicle_id}",
    response_model=WishlistActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an approved rental vehicle to traveler's wishlist (TR-16)",
)
@router.post(
    "/api/v1/wishlists/rentals/{vehicle_id}",
    response_model=WishlistActionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def add_rental_to_wishlist(
    vehicle_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Saves an approved rental vehicle to the authenticated traveler's personal wishlist.
    - Requires authenticated traveler.
    - Vehicle must exist and have approval_status == 'approved'.
    - Prevents duplicates (409 Conflict).
    - Synchronizes with central dbo.listing_references and dbo.wishlists.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found",
        )

    if vehicle.status == "retired":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Retired vehicles cannot be added to wishlist",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "rental_vehicle",
            ListingReference.target_id == vehicle.id,
        )
        .first()
    )
    if not listing_ref:
        listing_ref = sync_rental_listing_reference(db, vehicle)
        if not listing_ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rental vehicle cannot be saved to wishlist at this time",
            )
        db.flush()

    existing = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vehicle is already in your wishlist",
        )

    entry = Wishlist(
        user_id=current_user.id,
        listing_reference_id=listing_ref.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return WishlistActionResponse(
        message="Vehicle added to wishlist",
        vehicle_id=vehicle_id,
        saved=True,
        wishlist_id=entry.id,
    )


# ==============================================================================
# TR-16: REMOVE FROM WISHLIST
# ==============================================================================

@router.delete(
    "/api/v1/rental-wishlist/{vehicle_id}",
    response_model=WishlistActionResponse,
    summary="Remove a rental vehicle from traveler's wishlist (TR-16)",
)
@router.delete(
    "/api/v1/wishlists/rentals/{vehicle_id}",
    response_model=WishlistActionResponse,
    include_in_schema=False,
)
def remove_rental_from_wishlist(
    vehicle_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Removes a rental vehicle from the authenticated traveler's personal wishlist.
    """
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "rental_vehicle",
            ListingReference.target_id == vehicle_id,
        )
        .first()
    )
    if not listing_ref:
        return WishlistActionResponse(
            message="Vehicle removed from wishlist",
            vehicle_id=vehicle_id,
            saved=False,
        )

    entry = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if entry:
        db.delete(entry)
        db.commit()

    return WishlistActionResponse(
        message="Vehicle removed from wishlist",
        vehicle_id=vehicle_id,
        saved=False,
    )


# ==============================================================================
# TR-16: GET TRAVELER'S SAVED RENTAL VEHICLES
# ==============================================================================

@router.get(
    "/api/v1/rental-wishlist",
    response_model=RentalWishlistPagination,
    summary="List authenticated traveler's saved rental vehicles (TR-16)",
)
@router.get(
    "/api/v1/wishlists/rentals",
    response_model=RentalWishlistPagination,
    include_in_schema=False,
)
def get_my_rental_wishlist(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=50, description="Items per page")] = 20,
):
    """
    Returns paginated list of rental vehicles saved in the caller's wishlist,
    enriched with current vehicle details, pricing, cover photo, and availability.
    """
    query = (
        db.query(Wishlist, ListingReference, RentalVehicle)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .join(RentalVehicle, ListingReference.target_id == RentalVehicle.id)
        .filter(
            Wishlist.user_id == current_user.id,
            ListingReference.module_type == "rental_vehicle",
        )
    )

    total = query.count()
    pages = max(1, ceil(total / limit)) if total > 0 else 0

    results = (
        query.order_by(Wishlist.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    items: list[SavedRentalVehicleItem] = []
    for w, ref, v in results:
        primary_img = None
        if v.images:
            for img in v.images:
                if img.is_primary:
                    primary_img = img.image_url
                    break
            if not primary_img and len(v.images) > 0:
                primary_img = sorted(v.images, key=lambda x: x.display_order)[0].image_url

        loc_name = v.location.location_name if v.location else None
        loc_district = v.district or (v.location.district if v.location else ref.district)
        loc_city = v.city or (v.location.city if v.location else ref.city)

        is_avail = bool(v.approval_status == "approved" and v.status == "available")

        items.append(
            SavedRentalVehicleItem(
                wishlist_id=w.id,
                saved_at=w.created_at,
                vehicle_id=v.id,
                vehicle_name=v.vehicle_name,
                vehicle_type=v.vehicle_type,
                price_per_day=v.price_per_day,
                currency="LKR",
                status=v.status,
                district=loc_district,
                city=loc_city,
                location_name=loc_name,
                primary_image_url=primary_img,
                is_available=is_avail,
            )
        )

    return RentalWishlistPagination(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


# ==============================================================================
# TR-16: CHECK WISHLIST STATUS FOR VEHICLE
# ==============================================================================

@router.get(
    "/api/v1/rental-wishlist/{vehicle_id}/status",
    response_model=WishlistStatusResponse,
    summary="Check if a rental vehicle is saved in traveler's wishlist (TR-16)",
)
@router.get(
    "/api/v1/wishlists/rentals/{vehicle_id}/status",
    response_model=WishlistStatusResponse,
    include_in_schema=False,
)
def check_rental_wishlist_status(
    vehicle_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Checks whether a specific rental vehicle is currently saved in caller's wishlist.
    """
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "rental_vehicle",
            ListingReference.target_id == vehicle_id,
        )
        .first()
    )
    if not listing_ref:
        return WishlistStatusResponse(vehicle_id=vehicle_id, saved=False)

    saved = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
        is not None
    )

    return WishlistStatusResponse(vehicle_id=vehicle_id, saved=saved)
