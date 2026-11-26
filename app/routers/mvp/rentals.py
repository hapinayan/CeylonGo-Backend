import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select, true

from app.models.mvp import MVPHomestayWishlist, MVPListing, MVPListingVerification
from app.routers.mvp.activities import (
    published_listings, require_listing_wishlist, save_listing_wishlist,
)
from app.routers.mvp.homestays import DB, Traveler
from app.routers.rental_reviews import RentalReviewListResponse
from app.routers.rental_vehicles import rental_category_items
from app.schemas.mvp_rental import (
    RentalCard, RentalCategory, RentalDateCheck, RentalDateResult, RentalDetails,
    RentalInterval, RentalMetadata, RentalPage, RentalPhoto, RentalQuote, RentalSubtype,
    RentalWishlistAction, RentalWishlistStatus, Transmission,
)
from app.schemas.rental_vehicle import RentalCategoryItem
from app.services import mvp_rental
from app.services import mvp_listing_verification as verification

router = APIRouter(tags=["MVP Bike and Bicycle Rentals"])
Sort = Literal["newest", "price_asc", "price_desc", "rating", "popular"]


def _published():
    return published_listings(*sorted(mvp_rental.RENTAL_CATEGORIES))


def _vehicle(db, vehicle_id):
    verification.require_schema(db)
    vehicle = db.scalar(_published().where(MVPListing.id == vehicle_id))
    if vehicle is None:
        raise HTTPException(404, "Published rental vehicle not found")
    return vehicle


def _listing_data(data):
    return RentalMetadata.model_validate(data["rental"]) if data.get("rental") else None


def _photos(request, vehicle, data):
    photos = [
        photo for photo in verification.visible_media(vehicle)
        if photo.storage_key.startswith(verification.PHOTO_PREFIX)
    ]
    cover = data.get("cover_photo_id")
    if cover is not None:
        photos.sort(key=lambda photo: photo.id != cover)
    return [
        RentalPhoto(
            id=photo.id, vehicle_id=vehicle.id, caption=photo.alt_text,
            image_url=str(request.url_for(
                "public_photo", listing_id=vehicle.id, photo_id=photo.id,
            )),
            is_primary=index == 0, display_order=index,
        )
        for index, photo in enumerate(photos)
    ]


def _card(vehicle, rental, photos):
    category = vehicle.category
    if rental and category == "bike":
        category = rental.subtype
    if rental and rental.fuel_type and rental.fuel_type.casefold() == "electric":
        category = "electric"
    return RentalCard(
        id=vehicle.id, provider_id=vehicle.provider_id,
        provider_business_name=vehicle.provider.business_name,
        is_provider_verified=True,
        vehicle_type=vehicle.category, category=category,
        vehicle_subtype=rental.subtype if rental else None,
        vehicle_name=vehicle.title, description=vehicle.description,
        price_per_day=vehicle.price, currency=vehicle.currency, status="available",
        district=vehicle.district, city=vehicle.locality,
        primary_image_url=photos[0].image_url if photos else None,
        model_year=rental.model_year if rental else None,
        transmission=rental.transmission if rental else None,
        fuel_type=rental.fuel_type if rental else None,
        seating_capacity=rental.seating_capacity if rental else None,
        location_name=rental.pickup_name if rental else None,
        cancellation_policy=rental.cancellation_policy if rental else None,
        created_at=vehicle.created_at,
    )


def _page(request, db, query, page, limit, sort_by="newest"):
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = {
        "price_asc": (MVPListing.price.asc(),),
        "price_desc": (MVPListing.price.desc(),),
    }.get(sort_by, ())
    query = query.order_by(*order, MVPListing.id.desc())
    query = query.offset((page - 1) * limit).limit(limit)
    vehicles = db.scalars(query).all() if total else []
    vehicle_ids = [vehicle.id for vehicle in vehicles]
    data = {
        row.listing_id: json.loads(row.details) for row in db.scalars(
            select(MVPListingVerification).where(
                MVPListingVerification.listing_id.in_(vehicle_ids)
            )
        )
    } if vehicles else {}
    items = []
    for vehicle in vehicles:
        details = data.get(vehicle.id, {})
        items.append(_card(
            vehicle, _listing_data(details), _photos(request, vehicle, details),
        ))
    return RentalPage(
        total=total, page=page, limit=limit, pages=(total + limit - 1) // limit,
        items=items,
    )


@router.get("/rental-vehicles", response_model=RentalPage)
def discover(
    request: Request, db: DB,
    page: int = Query(1, ge=1, le=100000), limit: int = Query(20, ge=1, le=100),
    district: str | None = Query(None, max_length=100),
    city: str | None = Query(None, max_length=100),
    vehicle_type: RentalCategory | None = None,
    vehicle_subtype: RentalSubtype | None = None,
    transmission: Transmission | None = None,
    min_price: Decimal | None = Query(None, ge=0, max_digits=12, decimal_places=2),
    max_price: Decimal | None = Query(None, ge=0, max_digits=12, decimal_places=2),
    pickup_at: datetime | None = None, return_at: datetime | None = None,
    quantity: int = Query(1, ge=1, le=20),
    verified_only: bool = False, sort_by: Sort = "newest",
):
    verification.require_schema(db)
    query = _published()
    for value, column in ((district, MVPListing.district), (city, MVPListing.locality)):
        if value and (term := value.strip().lower()):
            query = query.where(func.lower(column).contains(term, autoescape=True))
    if vehicle_type:
        query = query.where(MVPListing.category == vehicle_type)
    if vehicle_subtype or transmission:
        query = query.join(MVPListingVerification)
        sql_server = db.get_bind().dialect.name == "mssql"
        extract = func.json_value if sql_server else func.json_extract
        if vehicle_subtype:
            query = query.where(extract(
                MVPListingVerification.details, "$.rental.subtype",
            ) == vehicle_subtype)
        if transmission:
            query = query.where(extract(
                MVPListingVerification.details, "$.rental.transmission",
            ) == transmission)
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422, "Minimum price exceeds maximum price")
    if min_price is not None:
        query = query.where(MVPListing.price >= min_price)
    if max_price is not None:
        query = query.where(MVPListing.price <= max_price)
    if (pickup_at is None) != (return_at is None):
        raise HTTPException(422, "Supply both pickup_at and return_at")
    if pickup_at is not None:
        start, end = mvp_rental.interval(pickup_at, return_at)
        units = mvp_rental.available_units(MVPListing.id, start, end, MVPListing)
        count = units.with_only_columns(func.count()).scalar_subquery()
        query = query.where(count >= quantity)
    return _page(request, db, query, page, limit, sort_by)


@router.get("/rental-vehicles/categories", response_model=list[RentalCategoryItem])
def categories(db: DB):
    verification.require_schema(db)
    published = _published().subquery()
    counts = dict(db.execute(select(
        published.c.category, func.count(),
    ).group_by(published.c.category)).all())
    return [
        item.model_copy(update={"sample_image_url": None})
        for item in rental_category_items(counts)
        if item.category in mvp_rental.RENTAL_CATEGORIES
    ]


@router.get("/rental-vehicles/{vehicle_id}", response_model=RentalDetails)
def details(vehicle_id: int, request: Request, db: DB):
    vehicle = _vehicle(db, vehicle_id)
    data = verification.details(db, vehicle)
    rental, photos = _listing_data(data), _photos(request, vehicle, data)
    return RentalDetails(
        **_card(vehicle, rental, photos).model_dump(), rental=rental, images=photos,
        requires_driving_license=vehicle.category == "bike",
        location={
            "location_name": rental.pickup_name if rental else vehicle.locality,
            "address": rental.pickup_address if rental else None,
            "district": vehicle.district, "city": vehicle.locality,
            "latitude": vehicle.latitude, "longitude": vehicle.longitude,
        },
    )


@router.get("/rental-vehicles/{vehicle_id}/images", response_model=list[RentalPhoto])
def images(vehicle_id: int, request: Request, db: DB):
    vehicle = _vehicle(db, vehicle_id)
    return _photos(request, vehicle, verification.details(db, vehicle))


@router.get(
    "/rental-vehicles/{vehicle_id}/reviews", response_model=RentalReviewListResponse,
)
def reviews(vehicle_id: int, db: DB, limit: int = Query(20, ge=1, le=100),
            page: int = Query(1, ge=1)):
    _vehicle(db, vehicle_id)
    return RentalReviewListResponse(items=[], total=0, average_rating=0, page=page,
                                    limit=limit, pages=0)


@router.get("/rental-vehicles/{vehicle_id}/availability", response_model=RentalQuote)
def availability(vehicle_id: int, db: DB, start_date: datetime, end_date: datetime,
                 quantity: int = Query(1, ge=1, le=20)):
    vehicle = _vehicle(db, vehicle_id)
    start, end = mvp_rental.interval(start_date, end_date)
    return mvp_rental.quote(db, vehicle, start, end, quantity)


@router.post(
    "/rental-vehicles/{vehicle_id}/calculate-quote", response_model=RentalQuote,
)
def quote(vehicle_id: int, payload: RentalInterval, db: DB):
    return availability(
        vehicle_id, db, payload.pickup_at, payload.return_at, payload.quantity,
    )


@router.post("/rental-bookings/validate-dates", response_model=RentalDateResult)
def validate_dates(payload: RentalDateCheck, db: DB):
    result = availability(
        payload.vehicle_id, db, payload.pickup_at, payload.return_at, payload.quantity,
    )
    collision = "none"
    if not result.is_available:
        unblocked = mvp_rental.available_quantity(
            db, payload.vehicle_id, result.pickup_at.replace(tzinfo=None),
            result.return_at.replace(tzinfo=None), ignore_reservations=True,
        )
        collision = "booked" if unblocked >= payload.quantity else "maintenance"
    return RentalDateResult(
        **payload.model_dump(exclude={"pickup_at", "return_at"}),
        pickup_at=result.pickup_at, return_at=result.return_at,
        is_valid=result.is_available, duration_days=result.duration_days,
        duration_hours=result.duration_hours,
        collision_type=collision,
        error_message=None if result.is_available else "Insufficient vehicles",
    )


@router.get("/rental-wishlist", response_model=RentalPage)
def wishlist(request: Request, db: DB, traveler: Traveler,
             page: int = Query(1, ge=1, le=100000),
             limit: int = Query(20, ge=1, le=100)):
    require_listing_wishlist(db)
    query = _published().join(MVPHomestayWishlist).where(
        MVPHomestayWishlist.traveler_id == traveler.id,
        MVPHomestayWishlist.is_saved == true(),
    )
    return _page(request, db, query, page, limit)


@router.get("/rental-wishlist/{vehicle_id}/status", response_model=RentalWishlistStatus)
def wishlist_status(vehicle_id: int, db: DB, traveler: Traveler):
    require_listing_wishlist(db)
    _vehicle(db, vehicle_id)
    row = db.get(MVPHomestayWishlist, (traveler.id, vehicle_id))
    return RentalWishlistStatus(vehicle_id=vehicle_id, saved=bool(row and row.is_saved))


@router.post(
    "/rental-wishlist/{vehicle_id}", response_model=RentalWishlistAction, status_code=201,
)
def save(vehicle_id: int, db: DB, traveler: Traveler):
    state = save_listing_wishlist(
        db, traveler, vehicle_id, True, *mvp_rental.RENTAL_CATEGORIES,
    )
    return RentalWishlistAction(
        vehicle_id=vehicle_id, saved=state.saved, message="Vehicle saved",
    )


@router.delete("/rental-wishlist/{vehicle_id}", response_model=RentalWishlistAction)
def remove(vehicle_id: int, db: DB, traveler: Traveler):
    state = save_listing_wishlist(
        db, traveler, vehicle_id, False, *mvp_rental.RENTAL_CATEGORIES,
    )
    return RentalWishlistAction(
        vehicle_id=vehicle_id, saved=state.saved, message="Vehicle removed",
    )
