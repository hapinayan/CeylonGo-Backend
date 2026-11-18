
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.routers.activity_bookings import get_my_activity_bookings


def test_traveler_can_view_own_activity_bookings():
    user = SimpleNamespace(id=10, role="traveler")
    db = MagicMock()

    booking = SimpleNamespace(id=1, user_id=10)
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        booking
    ]

    result = get_my_activity_bookings(
        current_user=user,
        db=db,
    )

    assert result == [booking]
    db.query.return_value.filter.assert_called_once()
    db.query.return_value.filter.return_value.order_by.assert_called_once()


def test_non_traveler_cannot_view_activity_bookings():
    user = SimpleNamespace(id=20, role="provider")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        get_my_activity_bookings(
            current_user=user,
            db=db,
        )

    assert exc.value.status_code == 403
    db.query.assert_not_called()

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.activity_booking import ActivityBooking


def test_booking_query_filters_by_current_user():
    user = SimpleNamespace(id=10, role="traveler")
    db = MagicMock()

    get_my_activity_bookings(current_user=user, db=db)

    query_filter = db.query.return_value.filter.call_args.args[0]

    assert query_filter.compare(ActivityBooking.user_id == 10)


def test_booking_query_orders_newest_first():
    user = SimpleNamespace(id=10, role="traveler")
    db = MagicMock()

    get_my_activity_bookings(current_user=user, db=db)

    order_expression = (
        db.query.return_value
        .filter.return_value
        .order_by.call_args.args[0]
    )

    assert str(order_expression).endswith("DESC")
