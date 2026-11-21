from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RentalIncidentType = Literal[
    "scratch",
    "dent",
    "accident",
    "mechanical_failure",
    "theft",
    "late_return",
    "traffic_fine",
]


class RentalIncidentCreateRequest(BaseModel):
    incident_type: RentalIncidentType = Field(
        ...,
        description="Type of incident: scratch, dent, accident, mechanical_failure, theft, late_return, traffic_fine",
    )
    description: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Detailed description of the incident, damage, circumstances, and location",
    )
    incident_datetime: datetime | None = Field(
        default=None,
        description="Datetime when the incident occurred (defaults to current server time if omitted)",
    )
    estimated_damage_cost: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        description="Estimated repair or replacement cost in LKR (must be >= 0)",
    )
    police_report_reference: str | None = Field(
        default=None,
        max_length=100,
        description="Official police report reference number or entry ID if filed",
    )
    evidence_urls: list[str] = Field(
        default_factory=list,
        description="Optional list of photo/document URLs supporting the incident report",
    )


class RentalIncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    incident_type: str
    incident_datetime: datetime
    description: str
    estimated_damage_cost: Decimal
    actual_repair_cost: Decimal | None = None
    police_report_reference: str | None = None
    resolution_status: str
    created_at: datetime
    updated_at: datetime


class RentalIncidentListResponse(BaseModel):
    booking_id: int
    total: int
    items: list[RentalIncidentResponse]
