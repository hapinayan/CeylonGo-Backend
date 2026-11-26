from datetime import datetime, time
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class OpeningDay(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    is_closed: bool = False
    open_time: time | None = None
    close_time: time | None = None

    @model_validator(mode="after")
    def validate_hours(self):
        for value in (self.open_time, self.close_time):
            if value is not None and value.tzinfo is not None:
                raise ValueError("Use Sri Lanka local clock times")
        if self.is_closed:
            self.open_time = self.close_time = None
        elif not self.open_time or not self.close_time or self.open_time >= self.close_time:
            raise ValueError("Closing time must be after opening time")
        return self


class ArtisanBusiness(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    craft_category: str | None = Field(None, min_length=1, max_length=100)
    business_type: str | None = Field(None, min_length=1, max_length=100)
    business_background: str | None = Field(None, max_length=2000)
    artisan_information: str | None = Field(None, max_length=2000)
    phone: str | None = Field(None, pattern=r"^\+?[0-9][0-9 ()-]{6,23}$")
    whatsapp_number: str | None = Field(None, pattern=r"^\+[1-9][0-9]{7,14}$")
    email: EmailStr | None = None
    website_url: AnyHttpUrl | None = None
    social_links: dict[Literal["facebook", "instagram", "tiktok", "youtube"], AnyHttpUrl] = Field(
        default_factory=dict,
    )
    opening_hours: str | None = Field(None, max_length=500)
    business_hours: list[OpeningDay] = Field(default_factory=list, max_length=7)

    @field_validator("website_url", "social_links")
    @classmethod
    def safe_links(cls, value):
        for url in value.values() if isinstance(value, dict) else [value]:
            if url and (url.username or url.password or len(str(url)) > 1000):
                raise ValueError("Use public URLs without embedded credentials")
        return value

    @model_validator(mode="after")
    def distinct_days(self):
        if len({day.day_of_week for day in self.business_hours}) != len(self.business_hours):
            raise ValueError("Each weekday can be configured once")
        self.business_hours.sort(key=lambda day: day.day_of_week)
        return self


class ArtisanImage(BaseModel):
    id: int
    artisan_listing_id: int
    image_url: str
    caption: str | None
    display_order: int
    is_cover: bool
    created_at: datetime


class ArtisanDetails(ArtisanBusiness):
    id: int
    provider_profile_id: int
    title: str
    description: str
    district: str
    city: str
    address: str | None
    latitude: float | None
    longitude: float | None
    price: float
    currency: Literal["LKR"] = "LKR"
    provider_business_name: str
    cover_image_url: str | None
    whatsapp_link: str | None
    is_active: bool
    is_verified: bool
    is_featured: Literal[False] = False
    approval_status: str
    average_rating: None = None
    total_reviews: Literal[0] = 0
    images: list[ArtisanImage]
    business_images: list[ArtisanImage]
    created_at: datetime
    updated_at: datetime


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    materials: str | None = Field(None, max_length=500)
    price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    is_active: bool = True


class ProductImage(BaseModel):
    id: int
    showcase_item_id: int
    image_url: str
    caption: str | None
    display_order: int
    is_primary: bool
    created_at: datetime


class ProductDetails(ProductInput):
    id: int
    artisan_listing_id: int
    listing_title: str
    item_type: Literal["product"] = "product"
    currency: Literal["LKR"] = "LKR"
    image_url: str | None
    price: float | None
    is_available: bool
    approval_status: str
    images: list[ProductImage]
    product_images: list[ProductImage]
    created_at: datetime
    updated_at: datetime


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class WishlistItem(BaseModel):
    id: int
    artisan_listing_id: int
    user_id: int
    is_available: bool
    listing: ArtisanDetails | None


class WishlistCheck(BaseModel):
    listing_id: int
    is_saved: bool
    wishlist_id: int | None
