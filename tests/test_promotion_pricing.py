
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.promotion_pricing import calculate_booking_price
from app.models.promotion import HomestayPromotion
from app.models.homestay import Homestay

def make_promotion(
    discount,
    start_date,
    end_date,
    is_active=True,
):
    return HomestayPromotion(
        id=1,
        homestay_id=1,
        title="Test Offer",
        discount_percent=Decimal(str(discount)),
        start_date=start_date,
        end_date=end_date,
        is_active=is_active,
    )


def test_price_without_promotion():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    today = date.today()

    result = calculate_booking_price(
        db=db,
        homestay_id=1,
        check_in=today + timedelta(days=1),
        check_out=today + timedelta(days=4),
        price_per_night=Decimal("5000.00"),
    )

    assert result == Decimal("15000.00")


def test_price_with_valid_promotion():
    db = MagicMock()

    today = date.today()
    check_in = today + timedelta(days=1)
    check_out = today + timedelta(days=4)

    promotion = make_promotion(
        discount="20.00",
        start_date=check_in,
        end_date=check_out - timedelta(days=1),
    )

    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = promotion

    result = calculate_booking_price(
        db=db,
        homestay_id=1,
        check_in=check_in,
        check_out=check_out,
        price_per_night=Decimal("5000.00"),
    )

    assert result == Decimal("12000.00")


def test_highest_discount_is_selected():
    db = MagicMock()

    today = date.today()
    check_in = today + timedelta(days=1)
    check_out = today + timedelta(days=4)

    promotion = make_promotion(
        discount="25.00",
        start_date=check_in,
        end_date=check_out - timedelta(days=1),
    )

    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = promotion

    result = calculate_booking_price(
        db=db,
        homestay_id=1,
        check_in=check_in,
        check_out=check_out,
        price_per_night=Decimal("5000.00"),
    )

    assert result == Decimal("11250.00")

    order_by_call = (
        db.query.return_value
        .filter.return_value
        .order_by
    )

    order_by_call.assert_called_once()


@pytest.mark.parametrize(
    "promotion_type",
    ["expired", "inactive", "partial_dates"],
)
def test_ineligible_promotions_are_excluded(promotion_type):
    db = MagicMock()

    today = date.today()
    check_in = today + timedelta(days=1)
    check_out = today + timedelta(days=4)

    # Simulate the database returning no eligible promotion.
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    result = calculate_booking_price(
        db=db,
        homestay_id=1,
        check_in=check_in,
        check_out=check_out,
        price_per_night=Decimal("5000.00"),
    )

    assert result == Decimal("15000.00")


def test_invalid_booking_dates():
    db = MagicMock()
    today = date.today()

    with pytest.raises(ValueError):
        calculate_booking_price(
            db=db,
            homestay_id=1,
            check_in=today + timedelta(days=5),
            check_out=today + timedelta(days=3),
            price_per_night=Decimal("5000.00"),
        )

    db.query.assert_not_called()
