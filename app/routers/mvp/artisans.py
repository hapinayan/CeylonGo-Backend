import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import case, false, func, or_, select, true
from sqlalchemy.orm import selectinload

from app.models.mvp import (
    MVPInventoryUnit, MVPListing, MVPListingMedia, MVPListingVerification, MVPHomestayWishlist,
)
from app.routers.mvp.activities import (
    published_listings, require_listing_wishlist, save_listing_wishlist,
)
from app.routers.mvp.homestays import Traveler
from app.routers.mvp.listing_management import DB, Provider
from app.routers.provider_registration import validate_and_save_image
from app.schemas.mvp_artisan import (
    ArtisanBusiness, ArtisanDetails, ArtisanImage, OpeningDay, Page, ProductDetails,
    ProductImage, ProductInput, WishlistCheck, WishlistItem,
)
from app.services import mvp_listing_verification as verification

router = APIRouter(tags=["MVP Artisans"])
ARTISAN = published_listings("artisan")


def _json(db, column, path):
    function = func.json_value if db.get_bind().dialect.name == "mssql" else func.json_extract
    return function(column, path)


def _craft(db):
    data = MVPListingVerification.details
    return func.coalesce(
        _json(db, data, "$.artisan.craft_category"),
        case((_json(db, data, "$.academic_seed.key").like("craft-%"), "Pottery & Ceramics")),
    )


def _page(db, query, page, size):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.scalars(query.offset((page - 1) * size).limit(size)).all()
    return rows, dict(total=total, page=page, page_size=size, total_pages=(total + size - 1) // size)


def _public(db, listing_id):
    verification.require_schema(db)
    listing = db.scalar(ARTISAN.where(MVPListing.id == listing_id))
    if listing is None:
        raise HTTPException(404, "Published artisan business not found")
    return listing


def _owned(db, provider, listing_id, *, lock=True):
    verification.require_schema(db)
    listing = verification.owned(db, provider, listing_id, lock=lock)
    if listing.category != "artisan":
        raise HTTPException(404, "Artisan business not found")
    if lock:
        verification.require_editable(listing)
    return listing


def _details_map(db, listings):
    ids = [row.id for row in listings]
    if not ids:
        return {}
    return {
        row.listing_id: json.loads(row.details)
        for row in db.scalars(
            select(MVPListingVerification).where(MVPListingVerification.listing_id.in_(ids))
        )
    }


def _business(db, request, listing, *, data=None):
    if data is None:
        data = verification.details(db, listing)
    fields = dict(data.get("artisan", {}))
    if not fields.get("craft_category") and data.get("academic_seed", {}).get("key", "").startswith(
        "craft-"
    ):
        fields["craft_category"] = "Pottery & Ceramics"
    settings = ArtisanBusiness.model_validate(fields)
    gallery = [
        media for media in verification.visible_media(listing)
        if media.storage_key.startswith(verification.PHOTO_PREFIX)
    ]
    cover_id = data.get("cover_photo_id")
    gallery.sort(key=lambda media: media.id != cover_id)
    images = [
        ArtisanImage(
            id=item.id, artisan_listing_id=listing.id, image_url=str(request.url_for(
                "public_photo", listing_id=listing.id, photo_id=item.id,
            )), caption=item.alt_text, display_order=item.sort_order,
            is_cover=index == 0, created_at=item.created_at,
        )
        for index, item in enumerate(gallery)
    ]
    return ArtisanDetails(
        **settings.model_dump(), id=listing.id, provider_profile_id=listing.provider_id,
        title=listing.title, description=listing.description, district=listing.district,
        city=listing.locality, address=data.get("address"), latitude=listing.latitude,
        longitude=listing.longitude, price=listing.price,
        provider_business_name=listing.provider.business_name,
        cover_image_url=images[0].image_url if images else None,
        whatsapp_link=(f"https://wa.me/{settings.whatsapp_number[1:]}"
                       if settings.whatsapp_number else None),
        is_active=listing.is_active, is_verified=listing.approval_status == "approved",
        approval_status=listing.approval_status, images=images, business_images=images,
        created_at=listing.created_at, updated_at=listing.updated_at,
    )


@router.get("/artisan-listings", response_model=Page[ArtisanDetails])
def discover(
    request: Request, db: DB, search: str | None = Query(None, max_length=150),
    category: str | None = Query(None, max_length=100),
    district: str | None = Query(None, max_length=80),
    business_type: str | None = Query(None, max_length=100),
    min_price: float | None = Query(None, ge=0, le=9999999999.99),
    max_price: float | None = Query(None, ge=0, le=9999999999.99),
    sort_by: Literal["newest", "title", "price_asc", "price_desc"] = "newest",
    page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
    is_featured: bool | None = None,
):
    verification.require_schema(db)
    query = ARTISAN.outerjoin(MVPListingVerification)
    if search and (term := search.strip().lower()):
        query = query.where(or_(*(func.lower(field).contains(term, autoescape=True)
                                 for field in (MVPListing.title, MVPListing.description,
                                               MVPListing.district, MVPListing.locality,
                                               _craft(db)))))
    for param, field in ((category, _craft(db)), (district, MVPListing.district), (
        business_type, _json(db, MVPListingVerification.details, "$.artisan.business_type"),
    )):
        if param and param.strip():
            query = query.where(func.lower(field) == param.strip().lower())
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422, "Minimum price exceeds maximum price")
    if min_price is not None:
        query = query.where(MVPListing.price >= min_price)
    if max_price is not None:
        query = query.where(MVPListing.price <= max_price)
    if min_price is not None or max_price is not None:
        query = query.where(MVPListing.price > 0)
    if is_featured:
        query = query.where(false())
    order = {"title": MVPListing.title.asc(), "price_asc": MVPListing.price.asc(),
             "price_desc": MVPListing.price.desc()}
    if sort_by in order:
        query = query.order_by(order[sort_by])
    rows, paging = _page(db, query.order_by(MVPListing.id.desc()), page, page_size)
    details_by_id = _details_map(db, rows)
    return Page[ArtisanDetails](
        items=[_business(db, request, row, data=details_by_id.get(row.id, {})) for row in rows],
        **paging,
    )


@router.get("/artisan-listings/categories", response_model=list[str])
def categories(db: DB):
    verification.require_schema(db)
    craft = _craft(db)
    query = ARTISAN.outerjoin(MVPListingVerification).with_only_columns(craft)
    return sorted(db.scalars(query.where(craft.is_not(None)).distinct()))


@router.get("/artisan-listings/districts", response_model=list[str])
def districts(db: DB):
    verification.require_schema(db)
    district = MVPListing.district
    return list(db.scalars(ARTISAN.with_only_columns(district).distinct().order_by(district)))


@router.get("/artisan-listings/{listing_id}", response_model=ArtisanDetails)
def details(listing_id: int, request: Request, db: DB):
    return _business(db, request, _public(db, listing_id))


@router.get("/artisan-listings/{listing_id}/business-hours", response_model=list[OpeningDay])
def business_hours(listing_id: int, db: DB):
    data = verification.details(db, _public(db, listing_id))
    hours = data.get("artisan", {}).get("business_hours", [])
    return sorted(hours, key=lambda day: day.get("day_of_week", 0))


def _wishlist_item(db, request, listing, traveler_id, *, data=None):
    available = (listing.is_active and listing.approval_status == "approved"
                 and verification.provider_eligible(listing))
    return WishlistItem(
        id=listing.id, artisan_listing_id=listing.id, user_id=traveler_id,
        is_available=available,
        listing=_business(db, request, listing, data=data) if available else None,
    )


@router.get("/artisan-wishlists/me", response_model=Page[WishlistItem])
def wishlist(
    request: Request, db: DB, traveler: Traveler, page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=100),
):
    require_listing_wishlist(db)
    query = select(MVPListing).join(MVPHomestayWishlist).where(
        MVPListing.category == "artisan", MVPHomestayWishlist.traveler_id == traveler.id,
        MVPHomestayWishlist.is_saved == true(),
    ).options(
        selectinload(MVPListing.provider),
        selectinload(MVPListing.media).undefer("*"),
    ).order_by(MVPListing.id.desc())
    rows, paging = _page(db, query, page, page_size)
    details_by_id = _details_map(db, rows)
    return Page[WishlistItem](
        items=[
            _wishlist_item(db, request, row, traveler.id, data=details_by_id.get(row.id, {}))
            for row in rows
        ],
        **paging,
    )


@router.get("/artisan-listings/{listing_id}/wishlist/check", response_model=WishlistCheck)
def check_wishlist(listing_id: int, db: DB, traveler: Traveler):
    require_listing_wishlist(db)
    listing = db.get(MVPListing, listing_id)
    if not listing or listing.category != "artisan":
        raise HTTPException(404, "Artisan business not found")
    record = db.get(MVPHomestayWishlist, (traveler.id, listing_id))
    saved = bool(record and record.is_saved)
    return WishlistCheck(listing_id=listing_id, is_saved=saved,
                         wishlist_id=listing_id if saved else None)


@router.post("/artisan-listings/{listing_id}/wishlist", response_model=WishlistItem, status_code=201)
def save_wishlist(listing_id: int, request: Request, db: DB, traveler: Traveler):
    save_listing_wishlist(db, traveler, listing_id, True, "artisan")
    return _wishlist_item(db, request, db.get(MVPListing, listing_id), traveler.id)


@router.delete("/artisan-listings/{listing_id}/wishlist", status_code=204)
def remove_wishlist(listing_id: int, db: DB, traveler: Traveler):
    save_listing_wishlist(db, traveler, listing_id, False, "artisan")
    return Response(status_code=204)


def _product_data(unit):
    return json.loads(unit.room_metadata or "{}")


def _unit(db, listing, product_id):
    unit = db.get(MVPInventoryUnit, product_id)
    if unit is None or unit.listing_id != listing.id or "artisan_product" not in _product_data(unit):
        raise HTTPException(404, "Artisan product not found")
    return unit


def _product_query(db):
    title = _json(db, MVPInventoryUnit.room_metadata, "$.artisan_product.title")
    return select(MVPInventoryUnit).where(title.is_not(None)).options(
        selectinload(MVPInventoryUnit.listing).selectinload(MVPListing.media).undefer("*"),
    )


def _photos(unit, request, *, owned=False):
    ids = _product_data(unit).get("photo_ids", [])
    files = {photo.id: photo for photo in unit.listing.media}
    images = []
    for index, photo_id in enumerate(ids):
        item = files.get(photo_id)
        if item is None or not item.storage_key.startswith(verification.PHOTO_PREFIX):
            continue
        path = (
            request.url_for("provider_artisan_product_photo", listing_id=unit.listing_id,
                            product_id=unit.id, photo_id=photo_id)
            if owned else request.url_for("artisan_product_photo", product_id=unit.id,
                                          photo_id=photo_id)
        )
        images.append(ProductImage(id=item.id, showcase_item_id=unit.id, image_url=str(path),
                                  caption=item.alt_text, display_order=index,
                                  is_primary=not images, created_at=item.created_at))
    return images


def _product_response(unit, request, *, owned=False):
    data = _product_data(unit)
    images = _photos(unit, request, owned=owned)
    approval = (
        "more_evidence_required"
        if unit.listing.approval_status == "evidence"
        else unit.listing.approval_status
    )
    return ProductDetails(
        **data["artisan_product"], id=unit.id, artisan_listing_id=unit.listing_id,
        listing_title=unit.listing.title, created_at=data["created_at"], updated_at=data["updated_at"],
        is_active=unit.is_active, is_available=unit.is_active, images=images, product_images=images,
        image_url=images[0].image_url if images else None, approval_status=approval,
    )


def _public_unit(db, product_id):
    verification.require_schema(db)
    unit = db.scalar(_product_query(db).where(
        MVPInventoryUnit.id == product_id, MVPInventoryUnit.is_active == true(),
        MVPInventoryUnit.listing_id.in_(ARTISAN.with_only_columns(MVPListing.id)),
    ))
    if unit is None:
        raise HTTPException(404, "Published artisan product not found")
    return unit


@router.get("/showcase-items", response_model=Page[ProductDetails])
def discover_products(
    request: Request, db: DB, search: str | None = Query(None, max_length=150),
    artisan_listing_id: int | None = Query(None, gt=0),
    item_type: Literal["product", "service"] | None = None,
    category: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
):
    verification.require_schema(db)
    query = _product_query(db).where(
        MVPInventoryUnit.is_active == true(),
        MVPInventoryUnit.listing_id.in_(ARTISAN.with_only_columns(MVPListing.id)),
    )
    if artisan_listing_id is not None:
        query = query.where(MVPInventoryUnit.listing_id == artisan_listing_id)
    if item_type == "service":
        query = query.where(false())
    data = MVPInventoryUnit.room_metadata
    if search and (term := search.strip().lower()):
        query = query.where(or_(*(func.lower(_json(db, data, f"$.artisan_product.{field}"))
                                  .contains(term, autoescape=True)
                                  for field in ("title", "description", "materials"))))
    if category and category.strip():
        query = query.where(func.lower(_json(db, data, "$.artisan_product.category"))
                            == category.strip().lower())
    units, paging = _page(db, query.order_by(MVPInventoryUnit.id.desc()), page, page_size)
    return Page[ProductDetails](
        items=[_product_response(unit, request) for unit in units], **paging,
    )


@router.get("/artisan-listings/{listing_id}/showcase-items", response_model=Page[ProductDetails])
def listing_products(
    listing_id: int, request: Request, db: DB, page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=100),
):
    _public(db, listing_id)
    return discover_products(request, db, None, listing_id, None, None, page, page_size)


@router.get("/showcase-items/{product_id}", response_model=ProductDetails)
def product_details(product_id: int, request: Request, db: DB):
    return _product_response(_public_unit(db, product_id), request)


@router.get("/showcase-items/{product_id}/images", response_model=list[ProductImage])
def product_images(product_id: int, request: Request, db: DB):
    return _photos(_public_unit(db, product_id), request)


def _product_file(unit, photo_id):
    if photo_id not in _product_data(unit).get("photo_ids", []):
        raise HTTPException(404, "Product photo not found")
    item = next((row for row in unit.listing.media if row.id == photo_id), None)
    if not item or not item.storage_key.startswith(verification.PHOTO_PREFIX):
        raise HTTPException(404, "Product photo not found")
    return FileResponse(verification.path_for(item.storage_key.removeprefix(verification.PHOTO_PREFIX)),
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/showcase-items/{product_id}/images/{photo_id}")
def artisan_product_photo(product_id: int, photo_id: int, db: DB):
    return _product_file(_public_unit(db, product_id), photo_id)


@router.get("/provider/listings/{listing_id}/products", response_model=Page[ProductDetails])
def provider_products(
    listing_id: int, request: Request, db: DB, provider: Provider,
    page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
):
    _owned(db, provider, listing_id, lock=False)
    query = _product_query(db).where(MVPInventoryUnit.listing_id == listing_id)
    units, paging = _page(db, query.order_by(MVPInventoryUnit.id.desc()), page, page_size)
    return Page[ProductDetails](
        items=[_product_response(unit, request, owned=True) for unit in units], **paging,
    )


@router.get("/provider/listings/{listing_id}/products/{product_id}", response_model=ProductDetails)
def provider_product(listing_id: int, product_id: int, request: Request, db: DB,
                     provider: Provider):
    listing = _owned(db, provider, listing_id, lock=False)
    return _product_response(_unit(db, listing, product_id), request, owned=True)


def _save_product(db, request, unit, payload):
    data = _product_data(unit)
    unit.is_active = payload.is_active
    data["artisan_product"] = payload.model_dump(mode="json", exclude={"is_active"})
    data["updated_at"] = verification.now().isoformat()
    data.setdefault("created_at", data["updated_at"])
    unit.room_metadata = json.dumps(data)
    verification.commit(db)
    return _product_response(unit, request, owned=True)


@router.post("/provider/listings/{listing_id}/products", response_model=ProductDetails, status_code=201)
def create_product(listing_id: int, payload: ProductInput, request: Request, db: DB,
                   provider: Provider):
    listing = _owned(db, provider, listing_id)
    unit = MVPInventoryUnit(listing_id=listing_id, provider_id=listing.provider_id,
                            label=f"Showcase {uuid4().hex}", capacity=1)
    db.add(unit)
    return _save_product(db, request, unit, payload)


@router.put("/provider/listings/{listing_id}/products/{product_id}", response_model=ProductDetails)
def edit_product(listing_id: int, product_id: int, payload: ProductInput, request: Request,
                 db: DB, provider: Provider):
    unit = _unit(db, _owned(db, provider, listing_id), product_id)
    return _save_product(db, request, unit, payload)


@router.delete("/provider/listings/{listing_id}/products/{product_id}", status_code=204)
def deactivate_product(listing_id: int, product_id: int, db: DB, provider: Provider):
    unit = _unit(db, _owned(db, provider, listing_id), product_id)
    unit.is_active = False
    verification.commit(db)
    return Response(status_code=204)


@router.post("/provider/listings/{listing_id}/products/{product_id}/images",
             response_model=ProductImage, status_code=201)
def add_product_photo(listing_id: int, product_id: int, request: Request, db: DB,
                      provider: Provider, file: UploadFile = File(...),
                      caption: str | None = Form(None, max_length=160)):
    unit = _unit(db, _owned(db, provider, listing_id), product_id)
    data = _product_data(unit)
    photos = data.setdefault("photo_ids", [])
    order = db.scalar(select(func.max(MVPListingMedia.sort_order)).where(
        MVPListingMedia.listing_id == listing_id,
    ))
    if len(photos) >= 12 or (order is not None and order >= 32767):
        raise HTTPException(409, "Product or listing photo limit reached")
    key = validate_and_save_image(file, "artisan_product", None)
    try:
        photo = MVPListingMedia(
            listing_id=listing_id, storage_key=verification.PHOTO_PREFIX + key,
            sort_order=0 if order is None else order + 1, alt_text=caption, is_visible=False,
        )
        db.add(photo)
        db.flush()
        photos.append(photo.id)
        unit.room_metadata = json.dumps(data)
        verification.commit(db)
    except Exception:
        verification.path_for(key).unlink(missing_ok=True)
        raise
    return _photos(unit, request, owned=True)[-1]


@router.get("/provider/listings/{listing_id}/products/{product_id}/images/{photo_id}")
def provider_artisan_product_photo(listing_id: int, product_id: int, photo_id: int, db: DB,
                                   provider: Provider):
    unit = _unit(db, _owned(db, provider, listing_id, lock=False), product_id)
    return _product_file(unit, photo_id)


@router.delete("/provider/listings/{listing_id}/products/{product_id}/images/{photo_id}",
               status_code=204)
def remove_product_photo(listing_id: int, product_id: int, photo_id: int, db: DB,
                         provider: Provider):
    unit = _unit(db, _owned(db, provider, listing_id), product_id)
    data = _product_data(unit)
    if photo_id not in data.get("photo_ids", []):
        raise HTTPException(404, "Product photo not found")
    data["photo_ids"].remove(photo_id)
    unit.room_metadata = json.dumps(data)
    verification.commit(db)
    return Response(status_code=204)
