"""
CeylonGo Academic MVP — Unified Listings & Inventory Router
Covers Homestay, Bike/Bicycle, Adventure, and Tour Guide services with units and slots.
"""

from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPListing, MVPUser
from app.schemas.mvp import (
    AvailabilitySlotCreate,
    AvailabilitySlotResponse,
    InventoryUnitCreate,
    InventoryUnitResponse,
    ListingCreateRequest,
    ListingMediaCreate,
    ListingMediaResponse,
    ListingResponse,
    ServiceCategory,
)
from app.services.mvp_services import MVPListingService
from app.services import mvp_listing_verification as verification

router = APIRouter(prefix="/listings", tags=["MVP Listings & Inventory"])


@router.post("", response_model=ListingResponse, status_code=status.HTTP_201_CREATED)
def create_listing(
    req: ListingCreateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    listing = MVPListingService.create_listing(db, current_user, req)
    return listing


@router.get("", response_model=list[ListingResponse])
def search_listings(
    category: ServiceCategory | None = Query(None),
    district: str | None = Query(None),
    max_price: Decimal | None = Query(None),
    limit: int = Query(50, le=100),
    db: Session = Depends(get_mvp_db),
):
    listings = MVPListingService.search_listings(db, category, district, max_price, limit)
    enabled = verification.installed(db)
    return [_public_response(listing, enabled) for listing in listings]


def _public_response(listing, enabled):
    response = ListingResponse.model_validate(listing)
    visible = {item.id for item in verification.visible_media(listing, enabled)}
    response.media = [item for item in response.media if item.id in visible]
    for item in response.media:
        if item.storage_key.startswith(verification.PHOTO_PREFIX):
            item.image_url = f"/api/v1/listings/{listing.id}/photos/{item.id}"
    response.units = [unit for unit in response.units if unit.is_active]
    return response


@router.get("/{listing_id}", response_model=ListingResponse)
def get_listing_details(listing_id: int, db: Session = Depends(get_mvp_db)):
    listing = db.query(MVPListing).filter(MVPListing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    if not listing.is_active or listing.approval_status != "approved" or not verification.provider_eligible(listing):
        raise HTTPException(404, "Published listing not found")
    return _public_response(listing, verification.installed(db))


@router.post("/{listing_id}/media", response_model=ListingMediaResponse, status_code=status.HTTP_201_CREATED)
def add_listing_media(
    listing_id: int,
    req: ListingMediaCreate,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    media = MVPListingService.add_media(db, current_user, listing_id, req)
    return media


@router.post("/{listing_id}/units", response_model=InventoryUnitResponse, status_code=status.HTTP_201_CREATED)
def add_inventory_unit(
    listing_id: int,
    req: InventoryUnitCreate,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    unit = MVPListingService.add_inventory_unit(db, current_user, listing_id, req)
    return unit


@router.post("/{listing_id}/slots", response_model=AvailabilitySlotResponse, status_code=status.HTTP_201_CREATED)
def add_availability_slot(
    listing_id: int,
    req: AvailabilitySlotCreate,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    slot = MVPListingService.add_availability_slot(db, listing_id, req, current_user)
    return slot
