import logging
import unicodedata
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.core.map_config import MapSettings
from app.database.connection import get_db
from app.models.user import User
from app.schemas.interactive_map import (
    Category,
    LocationCorrection,
    LocationDecision,
    LocationReview,
    LocationReviewPage,
    MapCategory,
    MapPage,
    MapPoint,
    MapRoute,
    Module,
    RouteRequest,
)
from app.services.map_catalog import CATEGORIES, list_locations
from app.services.map_location_management import (
    correct_location,
    decide_location,
    review_locations,
)
from app.services.map_routing import MapRoutingService, RouteError

router = APIRouter(prefix="/api/v1/map", tags=["Interactive Map"])
admin_router = APIRouter(prefix="/api/v1/admin/map", tags=["Map Administration"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[User, Depends(require_admin)]
Page = Annotated[int, Query(ge=1, le=10000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Latitude = Annotated[float, Query(ge=-90, le=90)]
Longitude = Annotated[float, Query(ge=-180, le=180)]
Radius = Annotated[int, Query(description="One of 5, 10, 25 or 50 kilometers")]


class NearbyAccessLogFilter(logging.Filter):
    """Remove traveler positions from Uvicorn's access-log request target."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, separator, _ = args[2].partition("?")
            if separator and path.rstrip("/").endswith("/api/v1/map/nearby"):
                record.args = (*args[:2], path, *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(NearbyAccessLogFilter())


@lru_cache(maxsize=1)
def get_routing_service() -> MapRoutingService:
    try:
        return MapRoutingService(MapSettings.from_env())
    except ValueError:
        raise HTTPException(
            503, detail={"code": "map_routing_not_configured"}
        ) from None


@router.get("/locations", response_model=MapPage)
def locations(
    db: Db,
    west: Longitude,
    south: Latitude,
    east: Longitude,
    north: Latitude,
    category: Category | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    if south > north:
        raise HTTPException(422, detail={"code": "map_invalid_bounds"})
    return list_locations(
        db,
        bounds=(west, south, east, north),
        category=category,
        page=page,
        page_size=page_size,
    )


@router.get("/nearby", response_model=MapPage)
def nearby(
    db: Db,
    lat: Latitude,
    lon: Longitude,
    radius_km: Radius = 10,
    category: Category | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    if radius_km not in (5, 10, 25, 50):
        raise HTTPException(422, detail={"code": "map_invalid_radius"})
    return list_locations(
        db,
        origin=MapPoint(latitude=lat, longitude=lon),
        radius=radius_km,
        category=category,
        page=page,
        page_size=page_size,
    )


@router.get("/search", response_model=MapPage)
def search(
    db: Db,
    q: Annotated[str, Query(max_length=120)],
    category: Category | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    query = " ".join(unicodedata.normalize("NFC", q).split())
    if not 2 <= len(query) <= 80 or not any(character.isalnum() for character in query):
        raise HTTPException(422, detail={"code": "map_invalid_search"})
    if any(unicodedata.category(character).startswith("C") for character in query):
        raise HTTPException(422, detail={"code": "map_invalid_search"})
    return list_locations(
        db, search=query, category=category, page=page, page_size=page_size
    )


@router.get("/categories", response_model=list[MapCategory])
def categories():
    return CATEGORIES


@router.post("/route", response_model=MapRoute)
async def route(
    payload: RouteRequest,
    service: Annotated[
        MapRoutingService,
        Depends(
            get_routing_service,
        ),
    ],
):
    try:
        return await service.route(payload)
    except RouteError as error:
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        raise HTTPException(
            error.status,
            detail={"code": error.code},
            headers=headers,
        ) from None


@admin_router.get("/locations", response_model=LocationReviewPage)
def admin_locations(
    db: Db,
    admin: Admin,
    module: Module | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    return review_locations(db, module, page, page_size)


@admin_router.patch("/locations/{module}/{listing_id}", response_model=LocationReview)
def admin_correct_location(
    db: Db,
    admin: Admin,
    module: Module,
    listing_id: Annotated[int, Path(gt=0)],
    payload: LocationCorrection,
):
    return correct_location(db, module, listing_id, payload, admin.id)


@admin_router.post(
    "/locations/{module}/{listing_id}/decision", response_model=LocationReview
)
def admin_decide_location(
    db: Db,
    admin: Admin,
    module: Literal["tour_guide", "rental"],
    listing_id: Annotated[int, Path(gt=0)],
    payload: LocationDecision,
):
    return decide_location(db, module, listing_id, payload, admin.id)
