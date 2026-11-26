from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.rental_vehicle import RentalVehicleListItem, RentalVehiclePagination

RentalCategory = Literal["bike", "bicycle"]
RentalSubtype = Literal["motorcycle", "scooter", "city", "mountain", "road", "hybrid"]
Transmission = Literal["manual", "automatic", "single_speed"]


class RentalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subtype: RentalSubtype
    transmission: Transmission | None = None
    model_year: int | None = Field(None, ge=1900, le=2100)
    seating_capacity: int | None = Field(None, ge=1, le=2)
    fuel_type: str | None = Field(None, max_length=30)
    specifications: str | None = Field(None, max_length=2000)
    pickup_name: str = Field(min_length=1, max_length=160)
    pickup_address: str | None = Field(None, max_length=300)
    requirements: str | None = Field(None, max_length=2000)
    rental_terms: str | None = Field(None, max_length=2000)
    cancellation_policy: str | None = Field(None, max_length=1000)


class RentalCard(RentalVehicleListItem):
    vehicle_type: RentalCategory
    category: str
    vehicle_subtype: RentalSubtype | None = None
    transmission: Transmission | None = None
    seating_capacity: int | None = None
    fuel_type: str | None = None
    free_cancellation_hours: int | None = None
    fuel_policy: Literal["same_to_same", "full_to_full", "not_applicable"] | None = None
    is_insured: bool | None = None


class RentalPage(RentalVehiclePagination):
    items: list[RentalCard]


class RentalPhoto(BaseModel):
    id: int
    vehicle_id: int
    image_url: str
    caption: str | None
    display_order: int
    is_primary: bool


class RentalPickup(BaseModel):
    location_name: str
    address: str | None
    district: str
    city: str
    latitude: Decimal | None
    longitude: Decimal | None


class RentalDetails(RentalCard):
    rental: RentalMetadata | None
    location: RentalPickup
    images: list[RentalPhoto]
    approval_status: Literal["approved"] = "approved"
    requires_driving_license: bool


class RentalInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pickup_at: datetime
    return_at: datetime
    quantity: int = Field(1, ge=1, le=20)


class RentalQuote(RentalInterval):
    vehicle_id: int
    is_available: bool
    available_quantity: int
    duration_days: int
    duration_hours: float
    rate: Decimal
    total_price: Decimal
    currency: Literal["LKR"] = "LKR"
    requires_driving_license: bool
    selected_plan_name: str = "Daily rate"
    security_deposit: None = None


class RentalDateCheck(RentalInterval):
    vehicle_id: int = Field(gt=0)


class RentalDateResult(RentalDateCheck):
    is_valid: bool
    duration_hours: float
    duration_days: int
    collision_type: Literal["none", "booked", "maintenance"]
    error_message: str | None


class RentalUnitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=100)
    is_active: bool


class RentalWishlistStatus(BaseModel):
    vehicle_id: int
    saved: bool


class RentalWishlistAction(RentalWishlistStatus):
    message: str
    wishlist_id: None = None
