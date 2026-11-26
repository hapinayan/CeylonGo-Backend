from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, true
from sqlalchemy.orm import selectinload

from app.models.mvp import MVPAvailabilitySlot, MVPHomestayWishlist, MVPListing
from app.routers.mvp.activities import (
    published_listings, require_listing_wishlist, save_listing_wishlist,
)
from app.routers.mvp.homestays import Traveler
from app.routers.mvp.listing_management import DB, Provider
from app.routers.mvp.listings import _public_response
from app.routers.tour_guide_public import list_tour_guide_categories
from app.schemas.mvp_guide import (
    GuideDetails, GuidePackage, GuidePackageDetails, GuidePage, GuideProfile, GuideSlot,
)
from app.schemas.mvp_homestay import StayDates, WishlistState
from app.schemas.tour_guide import GuideCategoryInfo
from app.services import mvp_guide, mvp_homestay
from app.services import mvp_listing_verification as verification

router = APIRouter(tags=["MVP Tour Guides"])
router.add_api_route(
    "/tour-guides/categories", list_tour_guide_categories,
    methods=["GET"], response_model=list[GuideCategoryInfo],
)


def _published():
    return published_listings("guide").options(
        selectinload(MVPListing.units).undefer("*")
    )


def _listing(db, listing_id):
    verification.require_schema(db)
    listing = db.scalar(_published().where(MVPListing.id == listing_id))
    if listing is None:
        raise HTTPException(404, "Published tour guide not found")
    return listing


def _packages(listing, *, public=False):
    units = sorted(listing.units, key=lambda unit: unit.id)
    return [
        item for unit in units if not public or unit.is_active
        if (item := verification.guide_package(unit)) is not None
    ]


def _details(db, listing):
    data = verification.details(db, listing)
    profile = data.get("guide")
    summary = _public_response(listing, True)
    summary.media = [photo for photo in summary.media if photo.image_url]
    if (cover := data.get("cover_photo_id")) is not None:
        summary.media.sort(
            key=lambda photo: (photo.id != cover, photo.sort_order, photo.id)
        )
    summary.units = []
    return GuideDetails(
        listing=summary, provider_name=listing.provider.business_name,
        profile=GuideProfile.model_validate(profile) if profile is not None else None,
        packages=_packages(listing, public=True),
    )


def _page(db, query, limit, offset):
    query = query.order_by(MVPListing.id.desc()).offset(offset).limit(limit + 1)
    listings = db.scalars(query).all()
    return GuidePage(
        items=[_details(db, listing) for listing in listings[:limit]],
        next_offset=offset + limit if len(listings) > limit else None,
    )


@router.get("/tour-guides", response_model=GuidePage)
def discover(
    db: DB, destination: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
):
    verification.require_schema(db)
    query = _published()
    if destination and (term := destination.strip().lower()):
        query = query.where(or_(
            func.lower(MVPListing.district).contains(term, autoescape=True),
            func.lower(MVPListing.locality).contains(term, autoescape=True),
        ))
    return _page(db, query, limit, offset)


@router.get("/tour-guides/wishlist", response_model=GuidePage)
def wishlist(db: DB, traveler: Traveler, limit: int = Query(20, ge=1, le=100),
             offset: int = Query(0, ge=0)):
    require_listing_wishlist(db)
    query = _published().join(MVPHomestayWishlist).where(
        MVPHomestayWishlist.traveler_id == traveler.id, MVPHomestayWishlist.is_saved == true(),
    )
    return _page(db, query, limit, offset)


@router.get("/tour-guides/{listing_id}", response_model=GuideDetails)
def details(listing_id: int, db: DB):
    return _details(db, _listing(db, listing_id))


@router.get("/tour-guides/{listing_id}/packages", response_model=list[GuidePackageDetails])
def public_packages(listing_id: int, db: DB):
    return _packages(_listing(db, listing_id), public=True)


@router.get(
    "/tour-guides/{listing_id}/packages/{package_id}", response_model=GuidePackageDetails,
)
def public_package(listing_id: int, package_id: int, db: DB):
    packages = public_packages(listing_id, db)
    item = next((item for item in packages if item.id == package_id), None)
    if item is None:
        raise HTTPException(404, "Active tour guide package not found")
    return item


@router.get("/tour-guides/{listing_id}/availability", response_model=list[GuideSlot])
def availability(
    listing_id: int, db: DB, package_id: int = Query(gt=0),
    date: date | None = None, participants: int = Query(1, ge=1, le=1000),
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
):
    listing = _listing(db, listing_id)
    packages = _packages(listing, public=True)
    item = next((item for item in packages if item.id == package_id), None)
    if item is None:
        raise HTTPException(404, "Active tour guide package not found")
    if participants > item.max_people:
        raise HTTPException(422, "Participant count exceeds the guide package capacity")
    query = mvp_guide.available_slots(listing, package_id)
    if date is not None:
        if date in {date.min, date.max}:
            raise HTTPException(422, "Guide date is outside the supported range")
        start, end = mvp_homestay.stay_window(
            StayDates(check_in=date, check_out=date + timedelta(days=1))
        )
        query = query.where(
            MVPAvailabilitySlot.starts_at >= start, MVPAvailabilitySlot.starts_at < end,
        )
    query = query.order_by(MVPAvailabilitySlot.starts_at, MVPAvailabilitySlot.id)
    return [
        mvp_guide.quote(slot, item, occupied, participants)
        for slot, occupied in db.execute(query.offset(offset).limit(limit))
    ]


@router.put("/tour-guides/{listing_id}/wishlist", response_model=WishlistState)
def save(listing_id: int, db: DB, traveler: Traveler):
    return save_listing_wishlist(db, traveler, listing_id, True, "guide")


@router.delete("/tour-guides/{listing_id}/wishlist", response_model=WishlistState)
def remove(listing_id: int, db: DB, traveler: Traveler):
    return save_listing_wishlist(db, traveler, listing_id, False, "guide")


def _owned_guide(db, provider, listing_id, *, lock):
    verification.require_schema(db)
    listing = verification.owned(db, provider, listing_id, lock=lock)
    if listing.category != "guide":
        raise HTTPException(404, "Tour guide listing not found")
    return listing


@router.get(
    "/provider/listings/{listing_id}/packages", response_model=list[GuidePackageDetails],
)
def packages(listing_id: int, db: DB, provider: Provider):
    return _packages(_owned_guide(db, provider, listing_id, lock=False))


@router.post(
    "/provider/listings/{listing_id}/packages", response_model=GuidePackageDetails,
    status_code=201,
)
def create_package(listing_id: int, payload: GuidePackage, db: DB, provider: Provider):
    listing = _owned_guide(db, provider, listing_id, lock=True)
    return mvp_guide.save_package(db, listing, payload)


@router.put(
    "/provider/listings/{listing_id}/packages/{package_id}",
    response_model=GuidePackageDetails,
)
def update_package(
    listing_id: int, package_id: int, payload: GuidePackage, db: DB, provider: Provider,
):
    listing = _owned_guide(db, provider, listing_id, lock=True)
    unit = next((unit for unit in listing.units if unit.id == package_id), None)
    if unit is None:
        raise HTTPException(404, "Tour guide package not found")
    return mvp_guide.save_package(db, listing, payload, unit)
