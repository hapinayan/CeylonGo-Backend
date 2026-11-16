
from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ============================================================
# M05 — LOCAL ARTISAN PROMOTION SCHEMAS
# ============================================================

class PromotionBase(BaseModel):
    title: str = Field(
        ..., min_length=2, max_length=150,
        description="Promotion or offer title"
    )
    description: str | None = Field(
        default=None, max_length=2000,
        description="Promotion details, terms, and conditions"
    )
    promo_code: str | None = Field(
        default=None, max_length=50,
        description="Optional promo code for travelers to quote"
    )
    discount_percentage: Decimal | None = Field(
        default=None, ge=Decimal("0.0"), le=Decimal("100.0"),
        description="Percentage discount (0.00 to 100.00)"
    )
    discount_amount: Decimal | None = Field(
        default=None, ge=Decimal("0.0"),
        description="Fixed discount amount in LKR"
    )
    start_date: datetime = Field(
        ..., description="Start date and time of the promotion"
    )
    end_date: datetime = Field(
        ..., description="End date and time of the promotion"
    )
    showcase_item_id: int | None = Field(
        default=None,
        description="Optional ID of a specific showcase item/service to which the offer applies"
    )

    @field_validator("title")
    @classmethod
    def validate_title_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Promotion title cannot be empty or blank")
        return trimmed

    @field_validator("description", "promo_code")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None

    @model_validator(mode="after")
    def validate_dates_and_discounts(self) -> "PromotionBase":
        if self.end_date < self.start_date:
            raise ValueError(
                "end_date must be greater than or equal to start_date"
            )

        if (
            self.discount_percentage is not None
            and self.discount_amount is not None
        ):
            raise ValueError(
                "A promotion cannot have both discount_percentage and discount_amount simultaneously. Choose one discount type."
            )

        return self


class PromotionCreate(PromotionBase):
    """Payload for creating a promotion linked to an artisan listing."""

    artisan_listing_id: int = Field(
        ...,
        description="ID of the parent artisan listing owned by the caller"
    )


class PromotionUpdate(BaseModel):
    """Payload for partial or full updates of an artisan promotion."""

    title: str | None = Field(
        default=None, min_length=2, max_length=150
    )
    description: str | None = Field(
        default=None, max_length=2000
    )
    promo_code: str | None = Field(
        default=None, max_length=50
    )
    discount_percentage: Decimal | None = Field(
        default=None, ge=Decimal("0.0"), le=Decimal("100.0")
    )
    discount_amount: Decimal | None = Field(
        default=None, ge=Decimal("0.0")
    )
    start_date: datetime | None = Field(default=None)
    end_date: datetime | None = Field(default=None)
    showcase_item_id: int | None = Field(default=None)
    is_active: bool | None = Field(
        default=None, description="Soft-delete or visibility toggle"
    )

    @field_validator("title")
    @classmethod
    def validate_title_non_empty(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            if not trimmed:
                raise ValueError(
                    "Promotion title cannot be empty or blank"
                )
            return trimmed
        return None

    @field_validator("description", "promo_code")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None

    @model_validator(mode="after")
    def validate_update_constraints(self) -> "PromotionUpdate":
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError(
                    "end_date must be greater than or equal to start_date"
                )

        if (
            self.discount_percentage is not None
            and self.discount_amount is not None
        ):
            raise ValueError(
                "A promotion cannot have both discount_percentage and discount_amount simultaneously. Choose one discount type."
            )

        return self


class PromotionSummary(BaseModel):
    """Compact summary schema for artisan promotion discovery."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    showcase_item_id: int | None = None
    title: str
    promo_code: str | None = None
    discount_percentage: Decimal | None = None
    discount_amount: Decimal | None = None
    start_date: datetime
    end_date: datetime
    is_active: bool


class PromotionResponse(PromotionBase):
    """Detailed response schema for an artisan promotion."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    listing_title: str | None = None
    showcase_item_title: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PromotionPagination(BaseModel):
    """Paginated response schema for artisan promotions."""

    items: list[PromotionSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


# ============================================================
# M01 — HOMESTAY PROMOTION SCHEMAS
# ============================================================

class PromotionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    homestay_id: int = Field(gt=0)

    title: str = Field(min_length=1, max_length=150)

    description: str | None = Field(
        default=None,
        max_length=1000,
    )

    discount_percent: Decimal = Field(
        gt=0,
        le=100,
        max_digits=5,
        decimal_places=2,
    )

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_promotion(self):
        self.title = self.title.strip()

        if not self.title:
            raise ValueError(
                "Promotion title cannot be empty"
            )

        if self.end_date < self.start_date:
            raise ValueError(
                "End date cannot be before start date"
            )

        return self


class PromotionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=150)

    description: str | None = Field(
        default=None,
        max_length=1000,
    )

    discount_percent: Decimal = Field(
        gt=0,
        le=100,
        max_digits=5,
        decimal_places=2,
    )

    start_date: date
    end_date: date
    is_active: bool = True

    @model_validator(mode="after")
    def validate_promotion(self):
        self.title = self.title.strip()

        if not self.title:
            raise ValueError(
                "Promotion title cannot be empty"
            )

        if self.end_date < self.start_date:
            raise ValueError(
                "End date cannot be before start date"
            )

        return self


class HomestayPromotionResponse(BaseModel):
    """Response schema for a Homestay promotion."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    homestay_id: int
    title: str
    description: str | None
    discount_percent: Decimal
    start_date: date
    end_date: date
    is_active: bool
    created_at: datetime


class PromotionListResponse(BaseModel):
    """Paginated response schema for Homestay promotions."""

    items: list[HomestayPromotionResponse]
    total: int
    page: int
    page_size: int
