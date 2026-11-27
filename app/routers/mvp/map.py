"""Guest map discovery over the published MVP catalog."""

import os
import re
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPInventoryUnit, MVPListing, MVPListingVerification
from app.routers.mvp.activities import published_listings
from app.schemas.mvp import ServiceCategory
from app.services import mvp_homestay, mvp_listing_verification as verification

router = APIRouter(prefix="/map", tags=["MVP Interactive Map"])
DETAIL_PATH = {
    "homestay": "homestays",
    "adventure": "activities",
    "guide": "tour-guides",
    "bike": "rental-vehicles",
    "bicycle": "rental-vehicles",
    "artisan": "artisan-listings",
}
PriceUnit = Literal[
    "per_night", "per_day", "per_participant", "per_person", "per_group"
]


class Marker(BaseModel):
    listing_id: int
    category: ServiceCategory
    title: str
    district: str
    latitude: float
    longitude: float
    cover_image_url: str | None
    display_price: Decimal | None
    pricing_unit: PriceUnit | None
    currency: str
    detail_url: str


def _bounds(bbox):
    if not re.fullmatch(
        r"(?:\s*[+-]?\d{1,3}(?:\.\d{1,12})?\s*,){3}"
        r"\s*[+-]?\d{1,3}(?:\.\d{1,12})?\s*",
        bbox,
    ):
        raise HTTPException(422, "bbox must be west,south,east,north")
    try:
        west, south, east, north = map(Decimal, bbox.split(","))
        if (
            all(value.is_finite() for value in (west, south, east, north))
            and -180 <= west < east <= 180
            and -90 <= south < north <= 90
        ):
            return west, south, east, north
    except (ArithmeticError, ValueError):
        pass
    raise HTTPException(
        422, "bbox must be west,south,east,north with increasing bounds"
    )


def _term(term):
    if term is None:
        return None
    value = " ".join(term.split())
    if not value.isprintable() or not any(char.isalnum() for char in value):
        raise HTTPException(422, "Search filters must contain letters or numbers")
    return value.lower()


def _price(listing):
    if listing.category == "artisan":
        return None, None
    if listing.category == "homestay":
        prices = [
            verification.room_details(room).nightly_price or listing.price
            for room in listing.units
            if room.is_active
        ]
        return min(prices, default=listing.price), "per_night"
    if listing.category == "guide":
        packages = [
            package
            for unit in listing.units
            if unit.is_active
            if (package := verification.guide_package(unit)) is not None
        ]
        if not packages:
            return listing.price, None
        cheapest = min(packages, key=lambda package: (package.price, package.id))
        return cheapest.price, cheapest.pricing_type
    unit = "per_participant" if listing.category == "adventure" else "per_day"
    return listing.price, unit


def _cover(listing, cover_id, media_base):
    photos = verification.visible_media(listing)
    photos.sort(key=lambda item: (item.id != cover_id, item.sort_order, item.id))
    for photo in photos:
        if photo.storage_key.startswith(verification.PHOTO_PREFIX):
            return f"/api/v1/listings/{listing.id}/photos/{photo.id}"
        url = mvp_homestay.listing_image_url(photo.storage_key, media_base)
        if url:
            return url
    return None


@router.get("/markers", response_model=list[Marker])
def markers(
    db: Annotated[Session, Depends(get_mvp_db)],
    category: ServiceCategory | None = None,
    district: Annotated[str | None, Query(max_length=80)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    bbox: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    query = (
        published_listings(category) if category else published_listings(*DETAIL_PATH)
    )
    query = query.where(
        MVPListing.latitude.between(-90, 90),
        MVPListing.longitude.between(-180, 180),
        or_(MVPListing.latitude != 0, MVPListing.longitude != 0),
    )
    if (district_name := _term(district)) is not None:
        query = query.where(func.lower(MVPListing.district) == district_name)
    if (keyword := _term(q)) is not None:
        pattern = keyword.translate(str.maketrans({c: "/" + c for c in "/%_["}))
        query = query.where(
            or_(
                *(
                    func.lower(column).contains(pattern, escape="/")
                    for column in (
                        MVPListing.title,
                        MVPListing.district,
                        MVPListing.locality,
                    )
                )
            )
        )
    if bbox is not None:
        west, south, east, north = _bounds(bbox)
        query = query.where(
            MVPListing.longitude.between(west, east),
            MVPListing.latitude.between(south, north),
        )
    query = query.options(
        selectinload(MVPListing.units).undefer(MVPInventoryUnit.room_metadata),
    )
    listings = db.scalars(query.order_by(MVPListing.id.desc()).limit(limit)).all()
    if not listings:
        return []
    reviews = {
        row.listing_id: row
        for row in db.scalars(
            select(MVPListingVerification).where(
                MVPListingVerification.listing_id.in_(
                    [listing.id for listing in listings]
                ),
            )
        )
    }
    media_base = os.getenv("MVP_LISTING_MEDIA_BASE_URL", "").rstrip("/")
    result = []
    for listing in listings:
        review = reviews.get(listing.id)
        cover_id = verification.parse_details(review).get("cover_photo_id")
        if listing.category == "homestay":
            cover_id = mvp_homestay.metadata(listing).cover_photo_id or cover_id
        price, unit = _price(listing)
        result.append(
            Marker(
                listing_id=listing.id,
                category=listing.category,
                title=listing.title,
                district=listing.district,
                latitude=listing.latitude,
                longitude=listing.longitude,
                cover_image_url=_cover(listing, cover_id, media_base),
                display_price=price,
                pricing_unit=unit,
                currency=listing.currency,
                detail_url=f"/api/v1/{DETAIL_PATH[listing.category]}/{listing.id}",
            )
        )
    return result
