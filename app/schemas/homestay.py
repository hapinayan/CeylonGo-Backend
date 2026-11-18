from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HomestayImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_url: str
    is_primary: bool


class HomestayAmenityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class HomestayProviderContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    homestay_id: int
    business_name: str
    phone: str
    secondary_phone: str | None = None
    business_district: str | None = None
    business_city: str | None = None
    description: str | None = None


class HomestayAvailabilityBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    block_type: str = Field(default="maintenance", max_length=50)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be strictly after start_date")
        valid_types = {
            "maintenance",
            "renovation",
            "seasonal_closure",
            "owner_unavailable",
            "admin_block",
            "other",
        }
        if self.block_type not in valid_types:
            raise ValueError(f"block_type must be one of {valid_types}")
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


class HomestayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    location: str
    province: str | None = None
    district: str | None = None
    city: str | None = None
    address: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    price_per_night: Decimal
    max_guests: int
    is_active: bool
    check_in_time: time | str | None = None
    check_out_time: time | str | None = None
    approval_status: str
    verification_status: str
    rejection_reason: str | None = None
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    created_at: datetime
    updated_at: datetime | None = None
    owner_id: int | None = None
    provider_profile_id: int | None = None
    is_sponsored: bool = False

    images: list[HomestayImageResponse] = Field(default_factory=list)
    amenities: list[HomestayAmenityResponse] = Field(default_factory=list)


class HomestayListResponse(BaseModel):
    items: list[HomestayResponse]
    total: int
    page: int
    page_size: int


class HomestayImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: str = Field(min_length=1, max_length=1000)
    is_primary: bool = False


class HomestayImageAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: str = Field(min_length=1, max_length=1000)
    is_primary: bool = False


class HomestayImageReorderItem(BaseModel):
    image_id: int
    display_order: int = Field(ge=0)


class HomestayImageReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HomestayImageReorderItem]


class HomestayCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    location: str = Field(min_length=1, max_length=255)
    province: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    price_per_night: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    max_guests: int = Field(gt=0)
    check_in_time: time | None = Field(default=time(14, 0))
    check_out_time: time | None = Field(default=time(11, 0))
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0)

    images: list[HomestayImageInput] = Field(default_factory=list, max_length=20)
    amenities: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_homestay(self):
        self.name = self.name.strip()
        self.location = self.location.strip()

        if not self.name or not self.location:
            raise ValueError("Name and location cannot be empty")

        if self.province:
            self.province = self.province.strip()
        if self.district:
            self.district = self.district.strip()
        if self.city:
            self.city = self.city.strip()
        if self.address:
            self.address = self.address.strip()

        if (self.latitude is None and self.longitude is not None) or (
            self.latitude is not None and self.longitude is None
        ):
            raise ValueError("Both latitude and longitude must be provided together")

        if self.amenities is not None:
            cleaned_amenities = [amenity.strip() for amenity in self.amenities]

            if any(not amenity or len(amenity) > 100 for amenity in cleaned_amenities):
                raise ValueError("Each amenity must contain 1 to 100 characters")

            seen_lower = set()
            for amenity in cleaned_amenities:
                if amenity.lower() in seen_lower:
                    raise ValueError("Duplicate amenities are not allowed")
                seen_lower.add(amenity.lower())

            self.amenities = cleaned_amenities

        if self.images is not None:
            if sum(image.is_primary for image in self.images) > 1:
                raise ValueError("Only one image can be primary")

            seen_urls = set()
            for image in self.images:
                cleaned_url = image.image_url.strip()
                if not cleaned_url:
                    raise ValueError("Image URL cannot be empty")
                if cleaned_url in seen_urls:
                    raise ValueError("Duplicate image URLs are not allowed")
                seen_urls.add(cleaned_url)
                image.image_url = cleaned_url

        return self


class HomestayUpdateRequest(HomestayCreateRequest):
    images: list[HomestayImageInput] | None = None
    amenities: list[str] | None = None
    is_active: bool = True


class HomestayReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(description="Must be 'approved', 'rejected', or 'suspended'")
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self):
        valid_decisions = {"approved", "rejected", "suspended"}
        if self.decision not in valid_decisions:
            raise ValueError(f"Decision must be one of {valid_decisions}")
        if self.decision == "rejected" and not (self.reason and self.reason.strip()):
            raise ValueError("Reason is required when rejecting a homestay")
        return self


class HomestayVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: str = Field(description="Must be 'verified', 'unverified', or 'rejected'")
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_status(self):
        valid = {"verified", "unverified", "rejected", "suspended"}
        if self.verification_status not in valid:
            raise ValueError(f"verification_status must be one of {valid}")
        return self
