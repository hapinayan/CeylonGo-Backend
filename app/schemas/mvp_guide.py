from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.mvp import ListingResponse
from app.schemas.tour_guide import (
    GuideCategorySlug, TourGuideLanguageCreate, TourGuidePackageCreate,
    TourGuideServiceAreaCreate,
)


class GuideProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    years_of_experience: int = Field(0, ge=0, le=100)
    categories: list[GuideCategorySlug] = Field(default_factory=list, max_length=4)
    languages: list[TourGuideLanguageCreate] = Field(default_factory=list, max_length=20)
    service_areas: list[TourGuideServiceAreaCreate] = Field(
        default_factory=list, max_length=30,
    )
    cancellation_policy: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def unique_languages_and_areas(self):
        codes = {language.language_code for language in self.languages}
        areas = {
            (area.district.casefold(), area.city.casefold()) for area in self.service_areas
        }
        if len(codes) != len(self.languages) or len(areas) != len(self.service_areas):
            raise ValueError("Guide languages and service areas must be unique")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("Guide categories must be unique")
        return self


class GuidePackage(TourGuidePackageCreate):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=100)
    itinerary: str | None = Field(None, max_length=4000)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Literal["LKR"] = "LKR"
    max_people: int = Field(10, ge=1, le=1000)


class GuidePackageDetails(GuidePackage):
    id: int


class GuideDetails(BaseModel):
    listing: ListingResponse
    provider_name: str
    profile: GuideProfile | None
    packages: list[GuidePackageDetails]


class GuidePage(BaseModel):
    items: list[GuideDetails]
    next_offset: int | None


class GuideSlot(BaseModel):
    id: int
    package_id: int
    starts_at: datetime
    ends_at: datetime
    available: bool
    capacity: int
    total_amount: Decimal
    currency: Literal["LKR"] = "LKR"
