"""
Pydantic v2 Contract Specifications for Module 04 (Eco-Tourism) & Module 03 (Activity Scheduling & Booking).

This module defines the formal integration contract, payload schemas, and validation rules
governing the interface between Module 04 (Eco-Tourism Listings) and Module 03 (Activity Scheduling & Booking).

STRICT ARCHITECTURAL ISOLATION:
- No database tables or migrations are created in this file.
- Contains pure Pydantic v2 schemas and validation logic for contract verification.
- Distinguishes confirmed M04 invariants from proposed M03 scheduling/booking contracts.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ==============================================================================
# 1. Slot Scheduling Contract Schemas (Provider Slot Management)
# ==============================================================================


class M03SlotCreateContract(BaseModel):
    """
    Contract payload for creating an activity slot for an approved EcoExperience.
    Submitted by the authenticated provider owning the eco experience.
    """

    model_config = ConfigDict(extra="forbid")

    eco_experience_id: int = Field(gt=0, description="Target EcoExperience identifier")
    start_datetime: datetime = Field(description="Slot start time in UTC")
    end_datetime: datetime = Field(description="Slot end time in UTC")
    max_capacity: int = Field(
        gt=0,
        le=50,
        description="Slot capacity bounded by the EcoExperience max_group_size (<= 50)",
    )
    guide_name: str | None = Field(
        default=None, max_length=150, description="Designated qualified guide"
    )

    @model_validator(mode="after")
    def validate_slot_timing(self) -> "M03SlotCreateContract":
        if self.end_datetime <= self.start_datetime:
            raise ValueError("end_datetime must be strictly greater than start_datetime")
        return self


class M03SlotResponseContract(BaseModel):
    """
    Contract representation of a scheduled activity slot returned to providers or travelers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    start_datetime: datetime
    end_datetime: datetime
    max_capacity: int
    booked_participants: int = 0
    available_capacity: int
    slot_status: Literal["available", "sold_out", "cancelled"]
    guide_name: str | None = None


# ==============================================================================
# 2. Traveler Booking Contract Schemas (Pay-at-Location Booking)
# ==============================================================================


class M03BookingRequestContract(BaseModel):
    """
    Contract payload submitted by an authenticated traveler to reserve an eco slot.
    P0 Scope: Strictly Pay-at-Location.
    """

    model_config = ConfigDict(extra="forbid")

    eco_experience_id: int = Field(gt=0)
    slot_id: int = Field(gt=0)
    participants: int = Field(
        gt=0,
        le=50,
        description="Number of participants in this booking (must not exceed available capacity)",
    )
    special_requests: str | None = Field(default=None, max_length=500)
    payment_method: Literal["pay_at_location"] = "pay_at_location"


class M03BookingResponseContract(BaseModel):
    """
    Contract response returned upon successful eco experience booking.
    """

    model_config = ConfigDict(from_attributes=True)

    booking_reference: str = Field(
        description="Standard reference format, e.g. ECO-YYYYMMDD-XXXX"
    )
    eco_experience_id: int
    slot_id: int
    user_id: int
    participants: int
    pricing_type: Literal["per_person", "per_group"]
    unit_price: Decimal
    total_price: Decimal
    currency: str = "LKR"
    payment_method: Literal["pay_at_location"] = "pay_at_location"
    payment_status: Literal["pending", "collected", "refunded"] = "pending"
    booking_status: Literal["confirmed", "cancelled", "completed"] = "confirmed"
    created_at: datetime
    cancellation_deadline: datetime
    meeting_point: str
    wildlife_safety_summary: str
    mandatory_safety_rules: list[str] = Field(default_factory=list)


class M03BookingCancelRequestContract(BaseModel):
    """
    Contract payload for a traveler requesting cancellation of an eco booking.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=500)


class M03BookingCancelResponseContract(BaseModel):
    """
    Contract response after an eco booking cancellation is processed.
    """

    model_config = ConfigDict(from_attributes=True)

    booking_reference: str
    booking_status: Literal["cancelled"]
    cancelled_at: datetime
    refund_status: Literal["not_applicable", "waived", "pending_review"]
    restored_capacity: int
    message: str


# ==============================================================================
# 3. M04-to-M03 Contract Business Logic & Validation Engine
# ==============================================================================


class M04toM03ContractValidator:
    """
    Pure validation functions embodying the M04-to-M03 integration contract rules.
    Used for contract verification, automated tests, and runtime bridge validation.
    """

    @staticmethod
    def calculate_total_price(
        pricing_type: str, base_price: Decimal, participants: int
    ) -> Decimal:
        """
        Enforce pricing calculation rules:
        - per_person: base_price * participants
        - per_group: base_price (fixed for the group reservation)
        """
        if pricing_type == "per_person":
            return base_price * Decimal(participants)
        elif pricing_type == "per_group":
            return base_price
        else:
            raise ValueError(f"Unsupported pricing_type '{pricing_type}'")

    @staticmethod
    def validate_slot_creation(
        experience_is_approved: bool,
        experience_is_published: bool,
        experience_max_group_size: int,
        requested_capacity: int,
        slot_start: datetime,
        slot_end: datetime,
        active_season_restrictions: list[dict] | None = None,
    ) -> dict[str, object]:
        """
        Validates whether a slot can be scheduled on an EcoExperience.
        """
        # Rule 1: Experience must be approved and published
        if not (experience_is_approved and experience_is_published):
            return {
                "allowed": False,
                "reason": "Cannot create slots for unapproved or unpublished eco experiences",
            }

        # Rule 2: Slot timing
        if slot_end <= slot_start:
            return {
                "allowed": False,
                "reason": "Slot end_datetime must be after start_datetime",
            }

        # Rule 3: Environmental capacity bound
        if requested_capacity > experience_max_group_size:
            return {
                "allowed": False,
                "reason": f"Slot capacity ({requested_capacity}) exceeds eco experience limit ({experience_max_group_size})",
            }

        if requested_capacity > 50:
            return {
                "allowed": False,
                "reason": "Slot capacity cannot exceed environmental hard limit of 50",
            }

        # Rule 4: Active seasonal restrictions (e.g., monsoon park closure, wildlife breeding)
        if active_season_restrictions:
            slot_date = slot_start.date()
            for restriction in active_season_restrictions:
                if (
                    restriction.get("is_active", True)
                    and restriction["start_date"] <= slot_date <= restriction["end_date"]
                ):
                    return {
                        "allowed": False,
                        "reason": f"Slot falls within active seasonal restriction: {restriction.get('reason', 'Restriction active')}",
                    }

        return {"allowed": True, "reason": None}

    @staticmethod
    def validate_booking_eligibility(
        experience_is_publicly_visible: bool,
        slot_status: str,
        available_capacity: int,
        requested_participants: int,
        experience_min_participants: int,
        experience_max_group_size: int,
    ) -> dict[str, object]:
        """
        Validates whether a traveler booking request meets all M04 eco rules.
        """
        # Rule 1: Public visibility
        if not experience_is_publicly_visible:
            return {
                "allowed": False,
                "reason": "Eco experience is not currently publicly available for booking",
            }

        # Rule 2: Slot availability
        if slot_status != "available":
            return {
                "allowed": False,
                "reason": f"Slot is not available for booking (status: '{slot_status}')",
            }

        # Rule 3: Capacity check
        if requested_participants > available_capacity:
            return {
                "allowed": False,
                "reason": f"Requested participants ({requested_participants}) exceeds available capacity ({available_capacity})",
            }

        # Rule 4: Group bounds
        if requested_participants > experience_max_group_size:
            return {
                "allowed": False,
                "reason": f"Requested participants exceeds maximum group size of {experience_max_group_size}",
            }

        return {"allowed": True, "reason": None}

    @staticmethod
    def evaluate_cancellation_policy(
        slot_start_datetime: datetime,
        cancellation_requested_at: datetime,
        free_cancellation_hours: int,
    ) -> dict[str, object]:
        """
        Evaluates free cancellation eligibility against the EcoExperience cutoff threshold.
        """
        deadline = slot_start_datetime - timedelta(hours=free_cancellation_hours)
        is_free_cancellation = cancellation_requested_at <= deadline

        return {
            "eligible_for_free_cancellation": is_free_cancellation,
            "cancellation_deadline": deadline,
            "hours_before_slot": (
                slot_start_datetime - cancellation_requested_at
            ).total_seconds()
            / 3600.0,
            "free_cancellation_hours": free_cancellation_hours,
        }
