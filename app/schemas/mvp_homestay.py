from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.schemas.mvp import (
    BookingResponse, InventoryUnitResponse, ListingMediaResponse, ListingResponse, PaymentResponse,
)

PropertyType = Literal["house", "apartment", "bungalow", "villa", "guesthouse", "cottage", "other"]
Amenity = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True,
                                           min_length=1, max_length=40)]


class HomestayMetadata(BaseModel):
    property_type: PropertyType | None = None
    cover_photo_id: int | None = Field(None, gt=0)
    amenities: list[Amenity] = Field(default_factory=list, max_length=30)

    @field_validator("amenities")
    @classmethod
    def deduplicate(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class HomestayHost(BaseModel):
    id: int
    business_name: str


class HomestayImage(ListingMediaResponse):
    image_url: str | None = None


class HomestayRoom(InventoryUnitResponse):
    room_type: str | None = None
    description: str | None = None
    nightly_price: Decimal | None = None
    photo_ids: list[int] = Field(default_factory=list)


class HomestayDetails(ListingResponse, HomestayMetadata):
    host: HomestayHost
    media: list[HomestayImage] = Field(default_factory=list)
    units: list[HomestayRoom] = Field(default_factory=list)


class HomestayPage(BaseModel):
    items: list[HomestayDetails]
    next_before_id: int | None


class StayDates(BaseModel):
    check_in: date
    check_out: date
    guests: int = Field(1, ge=1, le=50)


class HomestayQuote(StayDates):
    listing_id: int
    nights: int
    nightly_rate: Decimal
    total_amount: Decimal
    currency: Literal["LKR"] = "LKR"
    available_rooms: list[HomestayRoom]


class HomestayBookingRequest(StayDates):
    room_id: int = Field(gt=0)
    request_key: str = Field(min_length=10, max_length=36)
    expected_total: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)


class HomestayBookingDetails(BookingResponse):
    room_ids: list[int]
    payments: list[PaymentResponse]


class WishlistState(BaseModel):
    listing_id: int
    saved: bool
