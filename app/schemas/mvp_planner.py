from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.weather import DailyForecast

Service = Literal["homestay", "adventure", "guide", "bike", "bicycle", "artisan"]
Money = Annotated[Decimal, Field(ge=0, le=9999999999, decimal_places=2)]
Handle = Annotated[str, Field(pattern=r"^[sq][1-9][0-9]{0,2}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Title(Input):
    title: str = Field(min_length=1, max_length=120)


class Thread(Title):
    id: int
    created_at: datetime
    updated_at: datetime


class Message(Input):
    text: str = Field(min_length=1, max_length=2500)


class Search(Input):
    category: Service
    destination: str = Field(min_length=1, max_length=100)


class Selection(Input):
    listing_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=50)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    day: date | None = None
    room_id: int | None = Field(None, gt=0)
    slot_id: int | None = Field(None, gt=0)
    package_id: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def validate_times(self):
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("Supply both timestamps")
        if self.starts_at and (self.starts_at.utcoffset() is None
                               or self.ends_at.utcoffset() is None
                               or self.ends_at <= self.starts_at):
            raise ValueError("Use ordered ISO timestamps with offsets")
        return self


class Quote(Input):
    listing_id: int
    category: Service
    title: str
    starts_at: datetime
    ends_at: datetime
    quantity: int
    total_lkr: Money
    room_id: int | None = None
    slot_id: int | None = None
    package_id: int | None = None
    motorcycle_license_required: bool = False


class Proposal(Input):
    quote: Quote
    confirmation_token: str
    expires_at: datetime


class Confirm(Input):
    confirmation_token: str = Field(min_length=20, max_length=4096)
    confirm: Literal[True]
    motorcycle_license_confirmed: bool = False
    license_valid_until: date | None = None

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Confirm the displayed quote explicitly")
        return value


class PlanDay(Input):
    day: int = Field(ge=1, le=14)
    destination: str = Field(max_length=100)
    morning: str = Field(max_length=300)
    afternoon: str = Field(max_length=300)
    evening: str = Field(max_length=300)
    transport: str = Field(max_length=200)
    service_refs: list[Handle] = Field(default_factory=list, max_length=8)
    estimated_food_lkr: Money
    estimated_transport_lkr: Money
    estimated_other_lkr: Money


class Plan(Input):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(max_length=600)
    start_date: date | None = None
    travelers: int = Field(ge=1, le=50)
    budget_lkr: Money
    interests: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=10)
    accommodation_preference: str | None = Field(None, max_length=120)
    transport_preference: str | None = Field(None, max_length=120)
    travel_style: str | None = Field(None, max_length=120)
    days: list[PlanDay] = Field(min_length=1, max_length=14)
    alternatives: list[Handle] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def numbered_days(self):
        if [day.day for day in self.days] != list(range(1, len(self.days) + 1)):
            raise ValueError("Use consecutive itinerary days starting at 1")
        return self


class ServiceOption(Input):
    id: int
    title: str
    capacity: int
    advertised_price_lkr: Money | None = None
    pricing_type: str | None = None


class ServiceDetails(Input):
    amenities: list[str] = Field(default_factory=list)
    options: list[ServiceOption] = Field(default_factory=list)
    duration_minutes: int | None = None
    craft_category: str | None = None


class ListingResult(Input):
    ref: Handle
    listing_id: int
    category: Service
    title: str
    district: str
    locality: str
    advertised_price_lkr: Money | None
    latitude: float | None = None
    longitude: float | None = None
    details: ServiceDetails | None = None


class QuotedResult(Input):
    ref: Handle
    quote: Quote


class BookingStatus(Input):
    booking_id: int
    status: str
    total_lkr: Decimal
    payment_status: str | None
    payment_method: str | None


class ItineraryWeather(Input):
    day: int
    destination: str
    source: str
    fetched_at: datetime
    stale: bool
    forecast: DailyForecast


class Itinerary(Input):
    plan: Plan
    listings: list[ListingResult]
    quotes: list[QuotedResult]
    quoted_services_lkr: Decimal
    estimated_other_lkr: Decimal
    estimated_total_lkr: Decimal
    remaining_budget_lkr: Decimal
    weather: list[ItineraryWeather] = Field(default_factory=list)


class ModelReply(Input):
    message: str = Field(min_length=1, max_length=1200)
    itinerary: Plan | None = None
    booking_quote_ref: Handle | None = None


class Turn(Input):
    id: int
    user_message: str
    assistant_message: str
    itinerary: Itinerary | None = None
    proposal: Proposal | None = None
    listings: list[ListingResult]
    quotes: list[QuotedResult]
    bookings: list[BookingStatus]
    created_at: datetime


class SaveTrip(Title):
    turn_id: int = Field(gt=0)


class UpdateTrip(Title):
    turn_id: int | None = Field(None, gt=0)


class SavedTrip(Thread):
    itinerary: Itinerary
