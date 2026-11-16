
from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


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


class PromotionResponse(BaseModel):
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
    items: list[PromotionResponse]
    total: int
    page: int
    page_size: int
