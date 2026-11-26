from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.schemas.mvp import ListingResponse, ServiceCategory
from app.schemas.mvp_activity import ActivityMetadata
from app.schemas.mvp_guide import GuideProfile
from app.schemas.mvp_rental import RentalMetadata
from app.schemas.mvp_homestay import (
    HomestayBookingDetails, HomestayMetadata, HomestayRoom as RoomDetails,
)

LinkKind = Literal["maps", "facebook", "instagram", "website", "booking_com", "airbnb", "video", "other"]
FileKind = Literal[
    "customer_feedback", "business_certificate", "ownership", "authorization",
    "video", "safety", "credentials", "license", "workshop", "activity_photo", "other",
    "vehicle_registration", "insurance", "vehicle_photo",
]


class ListingDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ServiceCategory | None = None
    title: str | None = Field(None, max_length=160)
    description: str | None = Field(None, max_length=2000)
    district: str | None = Field(None, max_length=80)
    locality: str | None = Field(None, max_length=100)
    latitude: Decimal | None = Field(None, ge=-90, le=90, decimal_places=6)
    longitude: Decimal | None = Field(None, ge=-180, le=180, decimal_places=6)
    price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    province: str | None = Field(None, max_length=80)
    address: str | None = Field(None, max_length=300)
    house_rules: str | None = Field(None, max_length=1500)
    check_in_time: time | None = None
    check_out_time: time | None = None
    contact: str | None = Field(None, max_length=120)
    metadata: HomestayMetadata | None = None
    activity: ActivityMetadata | None = None
    guide: GuideProfile | None = None
    rental: RentalMetadata | None = None


class EvidenceLink(BaseModel):
    kind: LinkKind
    url: AnyHttpUrl

    @field_validator("url")
    @classmethod
    def public_url(cls, url):
        if url.username or url.password or len(str(url)) > 1000:
            raise ValueError("URL must be at most 1000 characters and contain no credentials")
        return url


class EvidenceUpdate(BaseModel):
    links: list[EvidenceLink] = Field(default_factory=list, max_length=20)
    notes: str = Field("", max_length=2500)


class RoomUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    capacity: int | None = Field(None, ge=1, le=50)
    is_active: bool | None = None
    room_type: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=1000)
    nightly_price: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    photo_ids: list[int] | None = Field(None, max_length=15)


class ReviewListing(BaseModel):
    listing: ListingResponse
    verification_status: str
    submitted_at: datetime | None
    decided_at: datetime | None
    decision_reason: str | None
    details: dict
    homestay_metadata: HomestayMetadata | None
    rooms: list[RoomDetails]


class ReviewProvider(BaseModel):
    id: int
    business_name: str
    email: str
    phone: str
    verification_status: str


class AdminReviewListing(ReviewListing):
    provider: ReviewProvider


class EvidenceFileResponse(BaseModel):
    id: str
    kind: FileKind
    media_type: str
    redacted: bool | None = None


class ListingDecisionHistory(BaseModel):
    actor_id: int
    reason: str
    occurred_at: datetime
    decision: str | None


class TravelerContact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: str
    email: str
    phone: str | None


class ProviderBookingDetails(HomestayBookingDetails):
    traveler_contact: TravelerContact | None = None
