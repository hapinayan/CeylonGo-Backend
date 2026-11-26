from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from app.core.mvp_security import require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPHomestayWishlist, MVPBooking, MVPListing, MVPUser
from app.schemas.mvp_homestay import (
    Amenity, HomestayBookingDetails, HomestayBookingRequest, HomestayDetails,
    HomestayMetadata, HomestayPage, HomestayQuote, PropertyType, StayDates, WishlistState,
)
from app.services import mvp_homestay as service
from app.services import mvp_listing_verification as verification

router = APIRouter(prefix="/homestays", tags=["MVP Homestays"])
DB = Annotated[Session, Depends(get_mvp_db)]
Traveler = Annotated[MVPUser, Depends(require_mvp_role("traveler"))]
Provider = Annotated[MVPUser, Depends(require_mvp_role("provider"))]
SCAN_LIMIT = 500


@router.get("", response_model=HomestayPage)
def search(
    db: DB,
    destination: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    check_in: date | None = None,
    check_out: date | None = None,
    guests: Annotated[int | None, Query(ge=1, le=50)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0, max_digits=12, decimal_places=2)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0, max_digits=12, decimal_places=2)] = None,
    amenities: Annotated[list[Amenity] | None, Query(max_length=30)] = None,
    property_type: PropertyType | None = None,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    if (check_in is None) != (check_out is None):
        raise HTTPException(422, "Supply both check_in and check_out")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422, "min_price must be less than or equal to max_price")
    window = None
    if check_in is not None and check_out is not None:
        window = service.stay_window(StayDates(
            check_in=check_in, check_out=check_out, guests=guests or 1,
        ))
        service.nights_for(*window)

    query = service.listing_details(service.public_query()).order_by(MVPListing.id.desc())
    if destination:
        term = destination.strip().lower()
        if not term:
            raise HTTPException(422, "Destination cannot be blank")
        query = query.where(or_(
            func.lower(MVPListing.district).contains(term, autoescape=True),
            func.lower(MVPListing.locality).contains(term, autoescape=True),
            func.lower(MVPListing.title).contains(term, autoescape=True),
        ))
    enabled = verification.installed(db)
    if before_id is not None:
        query = query.where(MVPListing.id < before_id)
    requested_amenities = set(amenities or [])
    items = []
    scanned, cursor = 0, None
    while scanned < SCAN_LIMIT:
        batch = query if cursor is None else query.where(MVPListing.id < cursor)
        rows = db.scalars(batch.limit(50)).all()
        for row in rows:
            scanned += 1
            cursor = row.id
            info = service.metadata(row)
            if property_type and info.property_type != property_type:
                continue
            if not requested_amenities.issubset(info.amenities):
                continue
            if window:
                rooms = [room for room, _ in service.available_rooms(db, row.id, *window, guests or 1).values()]
            else:
                rooms = [room for room in row.units if room.is_active and room.capacity >= (guests or 1)]
            if (window or guests is not None) and not rooms:
                continue
            rates = [verification.room_details(room, enabled).nightly_price or row.price for room in rooms]
            if not any(
                (min_price is None or rate >= min_price) and (max_price is None or rate <= max_price)
                for rate in (rates or [row.price])
            ):
                continue
            item = service.serialize(row, enabled)
            if rates:
                item.price = min(rate for rate in rates if
                                 (min_price is None or rate >= min_price)
                                 and (max_price is None or rate <= max_price))
            items.append(item)
            if len(items) == limit:
                more = db.scalar(query.where(MVPListing.id < cursor).limit(1))
                return HomestayPage(items=items, next_before_id=cursor if more else None)
        if len(rows) < 50:
            return HomestayPage(items=items, next_before_id=None)
    more = db.scalar(query.where(MVPListing.id < cursor).limit(1))
    return HomestayPage(items=items, next_before_id=cursor if more else None)


@router.get("/wishlist", response_model=HomestayPage)
def wishlist(
    db: DB,
    traveler: Traveler,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    query = service.listing_details(service.public_query()).join(
        MVPHomestayWishlist, MVPHomestayWishlist.listing_id == MVPListing.id
    ).where(
        MVPHomestayWishlist.traveler_id == traveler.id,
        MVPHomestayWishlist.is_saved == true(),
    )
    if before_id is not None:
        query = query.where(MVPListing.id < before_id)
    rows = db.scalars(query.order_by(MVPListing.id.desc()).limit(limit + 1)).all()
    enabled = verification.installed(db)
    return HomestayPage(
        items=[service.serialize(row, enabled) for row in rows[:limit]],
        next_before_id=rows[limit - 1].id if len(rows) > limit else None,
    )


@router.get("/{listing_id}", response_model=HomestayDetails)
def detail(listing_id: int, db: DB):
    return service.serialize(service.get_public(db, listing_id, details=True), verification.installed(db))


@router.get("/{listing_id}/availability", response_model=HomestayQuote)
def availability(
    listing_id: int, db: DB, check_in: date, check_out: date,
    guests: Annotated[int, Query(ge=1, le=50)] = 1,
    room_id: Annotated[int | None, Query(gt=0)] = None,
):
    listing = service.get_public(db, listing_id)
    return service.quote(db, listing, StayDates(
        check_in=check_in, check_out=check_out, guests=guests,
    ), room_id)


@router.put("/{listing_id}/metadata", response_model=HomestayMetadata)
def update_metadata(listing_id: int, payload: HomestayMetadata, db: DB, provider: Provider):
    listing = verification.owned(db, provider, listing_id, lock=True)
    if listing.category != "homestay":
        raise HTTPException(404, "Homestay not found")
    verification.require_editable(listing)
    previous = service.metadata(listing)
    if payload.cover_photo_id is not None and not any(
        item.id == payload.cover_photo_id
        for item in verification.visible_media(listing, verification.installed(db))
    ):
        raise HTTPException(422, "Cover photo must belong to this listing")
    if payload.property_type != previous.property_type or payload.amenities != previous.amenities:
        verification.invalidate_review(listing)
    listing.homestay_metadata = payload.model_dump_json()
    db.commit()
    return payload


@router.put("/{listing_id}/wishlist", response_model=WishlistState)
def save_to_wishlist(listing_id: int, db: DB, traveler: Traveler):
    service.get_public(db, listing_id, lock=True)
    entry = db.get(MVPHomestayWishlist, (traveler.id, listing_id))
    if entry is None:
        db.add(MVPHomestayWishlist(traveler_id=traveler.id, listing_id=listing_id))
    else:
        entry.is_saved = True
    db.commit()
    return WishlistState(listing_id=listing_id, saved=True)


@router.delete("/{listing_id}/wishlist", status_code=204)
def remove_from_wishlist(listing_id: int, db: DB, traveler: Traveler):
    listing = db.get(MVPListing, listing_id)
    if listing is None or listing.category != "homestay":
        return Response(status_code=204)
    entry = db.get(MVPHomestayWishlist, (traveler.id, listing_id))
    if entry is not None:
        entry.is_saved = False
        db.commit()
    return Response(status_code=204)


@router.post("/{listing_id}/bookings", response_model=HomestayBookingDetails, status_code=201)
def create_booking(listing_id: int, payload: HomestayBookingRequest, db: DB, traveler: Traveler):
    start, end = service.stay_window(payload)
    booking = service.book(
        db, traveler, listing_id, start, end, payload.guests, payload.request_key,
        payload.room_id, payload.expected_total,
    )
    return service.booking_details(booking)


@router.get("/{listing_id}/bookings/{booking_id}", response_model=HomestayBookingDetails)
def get_booking(listing_id: int, booking_id: int, db: DB, traveler: Traveler):
    booking = db.scalar(select(MVPBooking).join(MVPListing).where(
        MVPBooking.id == booking_id, MVPBooking.listing_id == listing_id,
        MVPBooking.traveler_id == traveler.id, MVPListing.category == "homestay",
    ))
    if not booking:
        raise HTTPException(404, "Homestay booking not found")
    return service.booking_details(booking)
