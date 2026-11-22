from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Mode = Literal["driving", "walking", "cycling"]
Module = Literal[
    "homestay", "activity", "eco_experience", "tour_guide", "artisan", "rental"
]
Category = Literal[
    "homestay",
    "adventure",
    "cultural",
    "local_activity",
    "community_activity",
    "eco_tourism",
    "tour_guide",
    "artisan",
    "bike_rental",
    "bicycle_rental",
    "local_business",
    "tourist_attraction",
]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class MapPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: Latitude
    longitude: Longitude


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: MapPoint
    destination: MapPoint
    mode: Mode = "driving"


class LineGeometry(BaseModel):
    type: Literal["MultiLineString"] = "MultiLineString"
    coordinates: list[
        Annotated[list[tuple[Longitude, Latitude]], Field(min_length=2)]
    ] = Field(min_length=1)


class RouteInstruction(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    distance_m: Nonnegative | None = None
    duration_seconds: Nonnegative | None = None


class Attribution(BaseModel):
    label: str
    url: str


class MapRoute(BaseModel):
    mode: Mode
    geometry: LineGeometry
    distance_m: Nonnegative
    distance_km: Nonnegative
    duration_seconds: Nonnegative
    duration_minutes: Nonnegative
    instructions: list[RouteInstruction]
    attribution: list[Attribution]


class MapCategory(BaseModel):
    id: Category
    label: str
    available: bool = True
    reason: str | None = None


class MapLocation(MapPoint):
    listing_id: int
    module_type: Module
    name: str
    category: Category
    address: str | None
    city: str | None
    district: str | None
    image_url: str | None
    rating: float | None
    price: float | None
    currency: str | None
    detail_path: str | None = None
    distance_km: Nonnegative | None = None


class MapPage(BaseModel):
    items: list[MapLocation]
    page: int
    page_size: int
    has_more: bool
    distance_type: Literal["straight_line"] | None = None


class LocationCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)
    reason: str = Field(min_length=3, max_length=500)


class LocationReview(BaseModel):
    listing_id: int
    module_type: Module
    name: str
    latitude: float | None
    longitude: float | None
    address: str | None
    city: str | None
    district: str | None
    approval_status: str
    active: bool


class LocationReviewPage(BaseModel):
    items: list[LocationReview]
    page: int
    page_size: int
    has_more: bool


class LocationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=3, max_length=500)
