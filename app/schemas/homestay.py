
from datetime import datetime
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

        if len(set(cleaned_amenities)) != len(cleaned_amenities):
            raise ValueError(
                "Duplicate amenities are not allowed"
            )

        self.amenities = cleaned_amenities

        if sum(image.is_primary for image in self.images) > 1:
            raise ValueError(
                "Only one image can be primary"
            )

        return self


class HomestayUpdateRequest(HomestayCreateRequest):
    is_active: bool = True
