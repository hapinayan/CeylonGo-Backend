
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.promotion import (
    PromotionCreateRequest,
    PromotionUpdateRequest,
)


@pytest.fixture
def valid_promotion_data():
    start = date.today() + timedelta(days=7)

    return {
        "homestay_id": 1,
        "title": "Summer Special",
        "description": "Special homestay discount",
        "discount_percent": "20.00",
        "start_date": start.isoformat(),
        "end_date": (
            start + timedelta(days=7)
        ).isoformat(),
    }


def test_valid_promotion_create(valid_promotion_data):
    promotion = PromotionCreateRequest(
        **valid_promotion_data
    )

    assert promotion.homestay_id == 1
    assert promotion.title == "Summer Special"
    assert promotion.discount_percent == Decimal("20.00")


def test_promotion_title_is_trimmed(valid_promotion_data):
    valid_promotion_data["title"] = "  Weekend Offer  "

    promotion = PromotionCreateRequest(
        **valid_promotion_data
    )

    assert promotion.title == "Weekend Offer"


@pytest.mark.parametrize(
    "invalid_discount",
    ["0", "-5", "101", "10.123"],
)
def test_invalid_discount_rejected(
    valid_promotion_data,
    invalid_discount,
):
    valid_promotion_data["discount_percent"] = (
        invalid_discount
    )

    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            **valid_promotion_data
        )


def test_end_date_before_start_rejected(
    valid_promotion_data,
):
    valid_promotion_data["end_date"] = (
        date.today() + timedelta(days=1)
    ).isoformat()

    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            **valid_promotion_data
        )


@pytest.mark.parametrize(
    "invalid_title",
    ["", "   "],
)
def test_empty_title_rejected(
    valid_promotion_data,
    invalid_title,
):
    valid_promotion_data["title"] = invalid_title

    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            **valid_promotion_data
        )


def test_invalid_homestay_id_rejected(
    valid_promotion_data,
):
    valid_promotion_data["homestay_id"] = 0

    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            **valid_promotion_data
        )


def test_unknown_fields_rejected(
    valid_promotion_data,
):
    valid_promotion_data["owner_id"] = 999

    with pytest.raises(ValidationError):
        PromotionCreateRequest(
            **valid_promotion_data
        )


def test_valid_promotion_update(valid_promotion_data):
    valid_promotion_data.pop("homestay_id")

    promotion = PromotionUpdateRequest(
        **valid_promotion_data,
        is_active=False,
    )

    assert promotion.is_active is False
    assert promotion.title == "Summer Special"


def test_update_invalid_dates_rejected(
    valid_promotion_data,
):
    valid_promotion_data.pop("homestay_id")

    valid_promotion_data["end_date"] = (
        date.today() + timedelta(days=1)
    ).isoformat()

    with pytest.raises(ValidationError):
        PromotionUpdateRequest(
            **valid_promotion_data
        )
