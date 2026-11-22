
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HomestayImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_url: str
    is_primary: bool


class HomestayAmenityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class HomestayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    location: str
    price_per_night: Decimal
    max_guests: int
    is_active: bool
    created_at: datetime
    owner_id: int | None = None
    provider_profile_id: int | None = None

    images: list[HomestayImageResponse] = Field(
        default_factory=list
    )

    amenities: list[HomestayAmenityResponse] = Field(
        default_factory=list
    )


class HomestayListResponse(BaseModel):
    items: list[HomestayResponse]
    total: int
    page: int
    page_size: int

from pydantic import model_validator


class HomestayImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: str = Field(
        min_length=1,
        max_length=1000,
    )
    is_primary: bool = False


class HomestayCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    location: str = Field(min_length=1, max_length=255)
    price_per_night: Decimal = Field(
        ge=0,
        max_digits=12,
        decimal_places=2,
    )
    max_guests: int = Field(gt=0)
    images: list[HomestayImageInput] = Field(
        default_factory=list,
        max_length=20,
    )
    amenities: list[str] = Field(
        default_factory=list,
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_homestay(self):
        self.name = self.name.strip()
        self.location = self.location.strip()

        if not self.name or not self.location:
            raise ValueError(
                "Name and location cannot be empty"
            )

        if self.amenities is not None:
            cleaned_amenities = [
                amenity.strip()
                for amenity in self.amenities
            ]

            if any(
                not amenity or len(amenity) > 100
                for amenity in cleaned_amenities
            ):
                raise ValueError(
                    "Each amenity must contain 1 to 100 characters"
                )

            seen_lower = set()
            for amenity in cleaned_amenities:
                if amenity.lower() in seen_lower:
                    raise ValueError(
                        "Duplicate amenities are not allowed"
                    )
                seen_lower.add(amenity.lower())

            self.amenities = cleaned_amenities

        if self.images is not None:
            if sum(image.is_primary for image in self.images) > 1:
                raise ValueError(
                    "Only one image can be primary"
                )

            seen_urls = set()
            for image in self.images:
                cleaned_url = image.image_url.strip()
                if not cleaned_url:
                    raise ValueError(
                        "Image URL cannot be empty"
                    )
                if cleaned_url in seen_urls:
                    raise ValueError(
                        "Duplicate image URLs are not allowed"
                    )
                seen_urls.add(cleaned_url)
                image.image_url = cleaned_url

        return self


class HomestayUpdateRequest(HomestayCreateRequest):
    images: list[HomestayImageInput] | None = None
    amenities: list[str] | None = None
    is_active: bool = True


class HomestayAvailabilityBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    block_type: str = Field(default="owner_unavailable", max_length=50)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_dates_and_type(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        if self.start_date < date.today():
            raise ValueError("Start date cannot be in the past")
        allowed = {
            "maintenance",
            "owner_unavailable",
            "admin_block",
            "renovation",
            "seasonal_closure",
            "other",
        }
        if self.block_type not in allowed:
            raise ValueError(f"Invalid block_type. Allowed types: {sorted(allowed)}")
        return self


class HomestayAvailabilityBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    homestay_id: int
    start_date: date
    end_date: date
    block_type: str
    reason: str | None = None
    is_active: bool
    created_at: datetime
