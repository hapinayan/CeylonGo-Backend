"""
Pydantic v2 schemas for Module 04 — Eco-Tourism Booking and Participant Management.

Strict Security & Architectural Rules:
- No database models or migrations are modified.
- extra='forbid' on all input models to prevent client privilege escalation or parameter tampering.
- No client-controlled pricing, payment status, booking status, or user_id fields.
- Total price and payment status are exclusively computed and managed server-side.
- Enforces participant capacity ceiling (1 <= participants <= 50).
- Minimum-age verification requirements are fully documented and wired to participant roster validation.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ENVIRONMENTAL_MAX_BOOKING_PARTICIPANTS = 50

EcoPaymentMethod = Literal[
    "pay_at_location",
    "online",
]

EcoPaymentStatus = Literal[
    "unpaid",
    "pending",
    "paid",
    "refund_pending",
    "refunded",
    "failed",
]

EcoBookingStatus = Literal[
    "pending",
    "confirmed",
    "rejected",
    "cancelled",
    "completed",
]


# ==============================================================================
# 1. Participant Roster Schemas
# ==============================================================================


class EcoParticipantCreate(BaseModel):
    """
    Individual participant registration details.
    Maps directly to physical table dbo.activity_booking_participants.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Full name of participant",
    )
    age: int | None = Field(
        default=None,
        ge=0,
        le=120,
        description=(
            "Participant age in years. Note: Validated against EcoExperience.min_age "
            "server-side via app.services.eco_booking_pricing."
        ),
    )
    emergency_contact_name: str | None = Field(
        default=None,
        max_length=150,
        description="Emergency contact person name",
    )
    emergency_contact_phone: str | None = Field(
        default=None,
        max_length=30,
        description="Emergency contact phone number",
    )
    special_requirements: str | None = Field(
        default=None,
        max_length=1000,
        description="Dietary, mobility, medical, or other special requirements",
    )
    waiver_signed: bool = Field(
        default=False,
        description="Whether participant or guardian has signed the required liability waiver",
    )

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Participant full_name cannot be empty or whitespace.")
        return cleaned


class EcoParticipantResponse(BaseModel):
    """
    Participant representation returned in booking details.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    age: int | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    special_requirements: str | None = None
    waiver_signed: bool
    created_at: datetime


# ==============================================================================
# 2. Traveler Booking Input & Response Schemas
# ==============================================================================


class EcoBookingCreate(BaseModel):
    """
    Payload for creating an Eco-Tourism reservation.
    Submitted by authenticated travelers.

    SECURITY RULES:
    1. total_price and currency are strictly server-calculated using Decimal precision.
    2. payment_status and booking_status are exclusively managed by the server.
    3. user_id is derived strictly from the authenticated JWT session.
    4. Participants count is bounded by the platform environmental ceiling (<= 50).
    5. Minimum-age verification: If participants_roster is provided, each participant's
       age is validated against the listing's min_age server-side.
    """

    model_config = ConfigDict(extra="forbid")

    eco_slot_id: int = Field(
        ...,
        gt=0,
        description="Identifier of the targeted active future scheduling slot.",
    )
    participants: int = Field(
        ...,
        ge=1,
        le=ENVIRONMENTAL_MAX_BOOKING_PARTICIPANTS,
        description="Number of participants reserved (must satisfy min_participants <= N <= max_group_size).",
    )
    payment_method: EcoPaymentMethod = Field(
        default="pay_at_location",
        description="Chosen payment method: 'pay_at_location' or 'online'.",
    )
    special_requests: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional traveler notes or special requests for the eco provider.",
    )
    participants_roster: list[EcoParticipantCreate] | None = Field(
        default=None,
        description=(
            "Optional detailed participant roster. If supplied, its length must match "
            "the participants count, and all ages must satisfy the listing's min_age."
        ),
    )

    @model_validator(mode="after")
    def validate_roster_count(self) -> "EcoBookingCreate":
        if self.participants_roster is not None:
            if len(self.participants_roster) != self.participants:
                raise ValueError(
                    f"Number of participants in roster ({len(self.participants_roster)}) "
                    f"does not match requested participant count ({self.participants})."
                )
        return self


class EcoBookingResponse(BaseModel):
    """
    Representation of an Eco-Tourism booking returned to travelers and providers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = Field(
        default=None,
        description="Canonical booking reference format, e.g. ECO-YYYYMMDD-XXXX",
    )
    eco_experience_id: int
    activity_slot_id: int
    user_id: int
    participants: int
    total_price: Decimal = Field(
        ...,
        description="Server-calculated total price quantized to 2 decimal places.",
    )
    currency: str = Field(default="LKR", max_length=3)
    payment_method: str
    payment_status: str
    booking_status: str
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    participants_list: list[EcoParticipantResponse] = Field(default_factory=list)


# ==============================================================================
# 3. Booking Cancellation Schema
# ==============================================================================


class EcoBookingCancellation(BaseModel):
    """
    Payload for traveler or provider requesting cancellation of an eco-experience booking.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Mandatory explanation for cancellation.",
    )

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 5:
            raise ValueError("Cancellation reason must contain at least 5 non-whitespace characters.")
        return cleaned


# ==============================================================================
# 4. Provider Action Schema
# ==============================================================================


class EcoBookingProviderDecision(BaseModel):
    """
    Payload for an eco provider accepting, rejecting, or confirming cash collection.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "reject", "confirm_cash"] = Field(
        ...,
        description="Provider booking lifecycle action.",
    )
    note: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional note or explanation (mandatory if action is 'reject').",
    )

    @model_validator(mode="after")
    def validate_rejection_note(self) -> "EcoBookingProviderDecision":
        if self.action == "reject":
            if not self.note or len(self.note.strip()) < 5:
                raise ValueError("A clear note (at least 5 characters) is required when rejecting a booking.")
        return self


# ==============================================================================
# 5. Provider Response Schemas
# ==============================================================================


class EcoBookingProviderListItem(BaseModel):
    """
    Representation of an eco-experience booking for provider review,
    listing, and decision management.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = Field(
        default=None,
        description="Canonical booking reference code, e.g. ECO-YYYYMMDD-XXXX.",
    )
    eco_experience_id: int
    eco_experience_title: str
    activity_slot_id: int
    slot_starts_at: datetime | None = None
    slot_ends_at: datetime | None = None
    traveler_id: int
    traveler_name: str | None = None
    traveler_email: str | None = None
    participants: int
    total_price: Decimal
    currency: str = Field(default="LKR", max_length=3)
    payment_method: str
    payment_status: str
    booking_status: str
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    participants_list: list[EcoParticipantResponse] = Field(default_factory=list)


class EcoBookingProviderListResponse(BaseModel):
    """
    Paginated response container for provider eco-experience bookings.
    """

    items: list[EcoBookingProviderListItem]
    total: int = Field(..., ge=0, description="Total matching booking count.")
    page: int = Field(..., ge=1, description="Current page number.")
    page_size: int = Field(..., ge=1, description="Page size limit.")

