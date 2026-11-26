import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Integer, and_, cast, func, or_, select, text, true
from sqlalchemy.orm import selectinload

from app.models.mvp import (
    MVPAvailabilitySlot, MVPHomestayWishlist, MVPListing, MVPListingVerification,
    MVPProviderProfile, MVPUser,
)
from app.routers.mvp.homestays import DB, Traveler
from app.schemas.mvp_activity import (
    ActivityCategory, ActivityDetails, ActivityMetadata, ActivityPage, ActivityPhoto,
    ActivitySlotDetails,
)
from app.schemas.mvp_homestay import WishlistState
from app.services import mvp_activity, mvp_homestay
from app.services import mvp_listing_verification as verification

router = APIRouter(prefix="/activities", tags=["MVP Activities"])


def published_listings(*categories):
    return select(MVPListing).where(
        MVPListing.category.in_(categories), MVPListing.approval_status == "approved",
        MVPListing.is_active == true(),
        MVPListing.provider.has(and_(
            MVPProviderProfile.verification_status == "approved",
            MVPProviderProfile.user.has(and_(MVPUser.role == "provider", MVPUser.is_active == true())),
        )),
    ).options(
        selectinload(MVPListing.provider),
        selectinload(MVPListing.media).undefer("*"),
    )


def _listing(db, listing_id):
    verification.require_schema(db)
    listing = db.scalar(published_listings("adventure").where(MVPListing.id == listing_id))
    if listing is None:
        raise HTTPException(404, "Published activity not found")
    return listing


def _details(listing, data):
    config = data.get("activity")
    media = verification.visible_media(listing)
    cover = data.get("cover_photo_id")
    if cover is not None:
        media.sort(key=lambda photo: photo.id != cover)
    return ActivityDetails(
        id=listing.id, title=listing.title, description=listing.description,
        district=listing.district, locality=listing.locality,
        latitude=listing.latitude, longitude=listing.longitude,
        price_per_participant=listing.price, currency=listing.currency,
        activity=ActivityMetadata.model_validate(config) if config is not None else None,
        photos=[
            ActivityPhoto(id=photo.id, alt_text=photo.alt_text,
                          image_url=f"/api/v1/listings/{listing.id}/photos/{photo.id}")
            for photo in media
            if photo.storage_key.startswith(verification.PHOTO_PREFIX)
        ],
        provider_id=listing.provider_id, provider_name=listing.provider.business_name,
    )


def require_listing_wishlist(db):
    verification.require_schema(db)
    if db.get_bind().dialect.name == "mssql" and db.scalar(text(
        "SELECT OBJECT_ID(N'dbo.homestay_wishlist', N'U')"
    )) is None:
        raise HTTPException(503, "Listing wishlist requires MVP schema 1.0.3")


def _page(db, query, limit, offset):
    items = db.scalars(query.offset(offset).limit(limit + 1)).all()
    data = {
        row.listing_id: json.loads(row.details)
        for row in db.scalars(select(MVPListingVerification).where(
            MVPListingVerification.listing_id.in_([item.id for item in items[:limit]])
        ))
    } if items else {}
    return ActivityPage(items=[_details(item, data.get(item.id, {})) for item in items[:limit]],
                        next_offset=offset + limit if len(items) > limit else None)


def _date_filter(day):
    if day in {date.min, date.max}:
        raise HTTPException(422, "Activity date is outside the supported range")
    start = mvp_homestay.utc_naive(datetime.combine(day, time.min, mvp_homestay.SRI_LANKA))
    end = start + timedelta(days=1)
    return and_(MVPAvailabilitySlot.starts_at >= start, MVPAvailabilitySlot.starts_at < end)


@router.get("", response_model=ActivityPage)
def discover(
    db: DB, destination: str | None = Query(None, min_length=1, max_length=100),
    category: ActivityCategory | None = None,
    date: date | None = None, participants: int | None = Query(None, ge=1, le=1000),
    min_price: Decimal | None = Query(None, ge=0),
    max_price: Decimal | None = Query(None, ge=0),
    sort: Literal["newest", "price_asc", "price_desc", "title"] = "newest",
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
):
    verification.require_schema(db)
    query = published_listings("adventure")
    if destination:
        term = destination.strip().lower()
        query = query.where(or_(*(func.lower(column).contains(term, autoescape=True)
                                  for column in (MVPListing.district, MVPListing.locality))))
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422, "Minimum price exceeds maximum price")
    if min_price is not None:
        query = query.where(MVPListing.price >= min_price)
    if max_price is not None:
        query = query.where(MVPListing.price <= max_price)
    if category or date or participants:
        query = query.join(MVPListingVerification)
        extract = func.json_value if db.get_bind().dialect.name == "mssql" else func.json_extract
        data = MVPListingVerification.details
        if category:
            query = query.where(extract(data, "$.activity.category") == category)
        if date or participants:
            minimum = cast(extract(data, "$.activity.min_participants"), Integer)
            maximum = cast(extract(data, "$.activity.max_participants"), Integer)
            quantity = participants if participants is not None else minimum
            slot_query = mvp_activity.slots().with_only_columns(MVPAvailabilitySlot.id).where(
                MVPAvailabilitySlot.listing_id == MVPListing.id,
                MVPAvailabilitySlot.is_open == true(), mvp_activity.remaining_capacity() >= quantity,
                minimum <= quantity, maximum >= quantity,
            ).correlate(MVPListing, MVPListingVerification)
            if date:
                slot_query = slot_query.where(_date_filter(date))
            query = query.where(slot_query.exists())
    order = {
        "newest": (), "price_asc": (MVPListing.price.asc(),),
        "price_desc": (MVPListing.price.desc(),), "title": (MVPListing.title.asc(),),
    }
    return _page(db, query.order_by(*order[sort], MVPListing.id.desc()), limit, offset)


@router.get("/wishlist", response_model=ActivityPage)
def wishlist(db: DB, traveler: Traveler, limit: int = Query(20, ge=1, le=100),
             offset: int = Query(0, ge=0)):
    require_listing_wishlist(db)
    query = published_listings("adventure").join(MVPHomestayWishlist).where(
        MVPHomestayWishlist.traveler_id == traveler.id,
        MVPHomestayWishlist.is_saved == true(),
    )
    return _page(db, query.order_by(MVPListing.id.desc()), limit, offset)


@router.get("/{listing_id}", response_model=ActivityDetails)
def details(listing_id: int, db: DB):
    listing = _listing(db, listing_id)
    return _details(listing, verification.details(db, listing))


@router.get("/{listing_id}/availability", response_model=list[ActivitySlotDetails])
def availability(
    listing_id: int, db: DB, date: date | None = None,
    participants: int | None = Query(None, ge=1, le=1000),
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
):
    listing = _listing(db, listing_id)
    config = mvp_activity.metadata(db, listing)
    if config is None:
        return []
    quantity = config.min_participants if participants is None else participants
    mvp_activity.validate_count(config, quantity)
    query = mvp_activity.slots().where(MVPAvailabilitySlot.listing_id == listing.id)
    if date:
        query = query.where(_date_filter(date))
    query = query.order_by(MVPAvailabilitySlot.starts_at, MVPAvailabilitySlot.id)
    return [mvp_activity.quote(listing, slot, remaining, quantity)
            for slot, remaining in db.execute(query.offset(offset).limit(limit))]


def save_listing_wishlist(db, traveler, listing_id, saved, *categories):
    require_listing_wishlist(db)
    listing = verification.locked_listing(db, listing_id)
    if listing.category not in categories:
        raise HTTPException(404, "Listing not found")
    if saved and not (
        listing.is_active and listing.approval_status == "approved"
        and verification.provider_eligible(listing)
    ):
        raise HTTPException(404, "Published listing not found")
    row = db.get(MVPHomestayWishlist, (traveler.id, listing_id))
    if row:
        row.is_saved = saved
    elif saved:
        db.add(MVPHomestayWishlist(traveler_id=traveler.id, listing_id=listing_id))
    verification.commit(db)
    return WishlistState(listing_id=listing_id, saved=saved)


@router.put("/{listing_id}/wishlist", response_model=WishlistState)
def save(listing_id: int, db: DB, traveler: Traveler):
    return save_listing_wishlist(db, traveler, listing_id, True, "adventure")


@router.delete("/{listing_id}/wishlist", response_model=WishlistState)
def remove(listing_id: int, db: DB, traveler: Traveler):
    return save_listing_wishlist(db, traveler, listing_id, False, "adventure")
