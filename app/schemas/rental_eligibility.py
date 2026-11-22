"""
Pydantic v2 schemas for CeylonGo Rental Driving Eligibility & Licensing (Phase 5 Batch 1).
Covers:
- AR-01: Driving License Verification
- AR-02: Minimum Age Requirements (Trusted DOB validation)
- AR-11: Tuk-Tuk Permit Verification (Domestic vs. Foreign IDP / AAC recognition permit)
"""

from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

EligibilityStatus = Literal["pending", "approved", "rejected", "expired"]
EligibilityDecision = Literal["approved", "rejected"]


class RentalEligibilitySubmitRequest(BaseModel):
    driver_license_number: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="National or foreign driving license number",
    )
    license_country: str = Field(
        default="Sri Lanka",
        min_length=2,
        max_length=100,
        description="Issuing country of the driving license",
    )
    license_expiry_date: date = Field(
        ...,
        description="Expiry date of the driving license (must be in the future)",
    )
    license_front_image: str | None = Field(
        default=None,
        max_length=1000,
        description="File reference or URL to front of driving license",
    )
    license_back_image: str | None = Field(
        default=None,
        max_length=1000,
        description="File reference or URL to back of driving license",
    )
    idp_number: str | None = Field(
        default=None,
        max_length=100,
        description="International Driving Permit (IDP) or AAC/DMT Recognition Permit number (required for foreign drivers renting tuk-tuks)",
    )
    date_of_birth: date | None = Field(
        default=None,
        description="Date of birth as stated on driving license or passport (AR-02)",
    )
    recognition_permit_number: str | None = Field(
        default=None,
        max_length=100,
        description="Automobile Association of Ceylon (AAC) recognition permit endorsement number (AR-11)",
    )
    recognition_permit_document_url: str | None = Field(
        default=None,
        max_length=1000,
        description="Private storage path to AAC recognition permit document image or PDF (AR-11)",
    )


class RentalEligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    driver_license_number_masked: str
    license_country: str
    license_expiry_date: date
    date_of_birth_masked: str | None = None
    has_idp: bool
    idp_number_masked: str | None = None
    has_recognition_permit: bool = False
    recognition_permit_number_masked: str | None = None
    verification_status: str
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    is_eligible_for_motorcycles: bool
    is_eligible_for_tuktuks: bool
    is_eligible_for_bicycles: bool = True
    created_at: datetime


class AdminEligibilityReviewRequest(BaseModel):
    decision: EligibilityDecision = Field(..., description="Decision: approved or rejected")
    rejection_reason: str | None = Field(
        default=None,
        max_length=1000,
        description="Mandatory explanation if the driving license or foreign permit is rejected",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Administrative verification notes",
    )
    verified_date_of_birth: date | None = Field(
        default=None,
        description="Admin-verified date of birth extracted from driving license or passport (AR-02)",
    )
    recognition_permit_number: str | None = Field(
        default=None,
        max_length=100,
        description="Admin-confirmed Automobile Association of Ceylon (AAC) recognition permit endorsement number (AR-11)",
    )


class AdminEligibilityReviewResponse(BaseModel):
    id: int
    user_id: int
    driver_license_number: str
    license_country: str
    previous_status: str
    new_status: str
    reviewed_by: int
    reviewed_at: datetime
    rejection_reason: str | None = None
    message: str


class AdminEligibilityItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    traveler_name: str
    traveler_email: str
    driver_license_number: str
    license_country: str
    license_expiry_date: date
    date_of_birth: date | None = None
    idp_number: str | None = None
    recognition_permit_number: str | None = None
    recognition_permit_document_url: str | None = None
    license_front_image: str | None = None
    license_back_image: str | None = None
    verification_status: str
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime


class AdminEligibilityPagination(BaseModel):
    items: list[AdminEligibilityItem]
    total: int
    page: int
    page_size: int
    total_pages: int
