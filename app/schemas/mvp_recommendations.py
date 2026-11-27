import re
import unicodedata
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

from app.schemas.mvp_account import Input
from app.schemas.mvp_activity import ActivityCategory
from app.schemas.mvp_rental import RentalSubtype


Category = Literal["homestay", "adventure", "guide", "bike", "bicycle", "artisan"]
RecommendationType = Literal[
    "recommended_for_you",
    "because_you_viewed",
    "based_on_your_interests",
    "favorite_destinations",
    "popular_experiences",
    "discover_something_new",
]
PricingUnit = Literal[
    "per_night", "per_day", "per_participant", "per_person", "per_group"
]
ListingID = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", value.replace("_", " ")).split())


class SearchFilters(Input):
    activity_category: ActivityCategory | None = None
    rental_subtype: RentalSubtype | None = None
    min_price: int | None = Field(None, strict=True, ge=0, le=1_000_000_000)
    max_price: int | None = Field(None, strict=True, ge=0, le=1_000_000_000)
    pricing_unit: PricingUnit | None = None

    @model_validator(mode="after")
    def price_range(self):
        has_range = self.min_price is not None or self.max_price is not None
        if has_range != (self.pricing_unit is not None):
            raise ValueError("Price filters require a range and its pricing unit")
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("Minimum price exceeds maximum price")
        return self


class SearchEvent(Input):
    event_type: Literal["search"]
    category: Category | None = None
    search_query: str | None = Field(None, max_length=120)
    district: str | None = Field(None, max_length=80)
    destination: str | None = Field(None, max_length=80)
    filters: SearchFilters | None = None

    @field_validator("search_query", "district", "destination")
    @classmethod
    def search_text(cls, value, info):
        if value is None:
            return None
        canonical = unicodedata.normalize("NFKC", value)
        # Keep tourism search phrases and reject common contact and credential forms.
        digits = sum(char.isdecimal() for char in canonical)
        if (
            digits > 4
            or not all(
                char.isalpha()
                or char.isdecimal()
                or char.isspace()
                or unicodedata.category(char).startswith("M")
                or char in "-'’"
                for char in canonical
            )
            or any(len(word) > 32 for word in canonical.split())
        ):
            raise ValueError(
                "Use a short tourism search phrase without contact details"
            )
        normalized = normalize(canonical)
        if len(normalized) > (120 if info.field_name == "search_query" else 80):
            raise ValueError("Normalized search text is too long")
        return normalized or None

    @model_validator(mode="after")
    def has_signal(self):
        if not any(
            (
                self.category,
                self.search_query,
                self.district,
                self.destination,
                self.filters and self.filters.model_dump(exclude_none=True),
            )
        ):
            raise ValueError("Provide a search term, category, location, or filter")
        return self


class ViewEvent(Input):
    event_type: Literal["view"]
    listing_id: ListingID
    category: Category | None = None


ActivityInput = Annotated[SearchEvent | ViewEvent, Field(discriminator="event_type")]


class PersonalizationSettings(Input):
    behavioral_personalization: StrictBool


class ActivityResult(BaseModel):
    recorded: bool
    reason: Literal["recorded", "duplicate", "personalization_disabled"]


class RecommendationCard(BaseModel):
    listing_id: int
    category: Category
    title: str
    short_description: str
    district: str
    location: str
    cover_image_url: str | None
    starting_price: Decimal | None
    currency: str | None
    pricing_unit: PricingUnit | None
    rating: float | None = None
    recommendation_score: float
    recommendation_reason: str
    detail_route: str
    bookable: bool


class RecommendationGroup(BaseModel):
    recommendation_type: RecommendationType | Literal["similar_services"]
    items: list[RecommendationCard]


class HomeRecommendations(BaseModel):
    sections: list[RecommendationGroup]
