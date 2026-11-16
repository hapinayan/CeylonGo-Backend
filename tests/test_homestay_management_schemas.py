
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.homestay import (
    HomestayCreateRequest,
    HomestayUpdateRequest,
)


def valid_homestay():
    return {
        "name": "Jaffna Family Homestay",
        "description": "Comfortable family accommodation",
        "location": "Jaffna",
        "price_per_night": "5500.00",
        "max_guests": 4,
        "images": [
            {
                "image_url": "https://example.com/room.jpg",
                "is_primary": True,
            }
        ],
        "amenities": ["WiFi", "Parking"],
    }


def test_valid_homestay_creation():
    data = HomestayCreateRequest(**valid_homestay())

    assert data.name == "Jaffna Family Homestay"
    assert data.price_per_night == Decimal("5500.00")
    assert data.amenities == ["WiFi", "Parking"]


def test_name_and_location_are_trimmed():
    payload = valid_homestay()
    payload["name"] = "  Jaffna Stay  "
    payload["location"] = "  Jaffna  "

    data = HomestayCreateRequest(**payload)

    assert data.name == "Jaffna Stay"
    assert data.location == "Jaffna"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "   "),
        ("location", "   "),
        ("price_per_night", "-100"),
        ("max_guests", 0),
    ],
)
def test_invalid_homestay_fields(field, value):
    payload = valid_homestay()
    payload[field] = value

    with pytest.raises(ValidationError):
        HomestayCreateRequest(**payload)


def test_duplicate_amenities_are_rejected():
    payload = valid_homestay()
    payload["amenities"] = ["WiFi", "WiFi"]

    with pytest.raises(ValidationError):
        HomestayCreateRequest(**payload)


def test_multiple_primary_images_are_rejected():
    payload = valid_homestay()
    payload["images"].append(
        {
            "image_url": "https://example.com/room2.jpg",
            "is_primary": True,
        }
    )

    with pytest.raises(ValidationError):
        HomestayCreateRequest(**payload)


def test_owner_id_cannot_be_supplied():
    payload = valid_homestay()
    payload["owner_id"] = 999

    with pytest.raises(ValidationError):
        HomestayCreateRequest(**payload)


def test_valid_homestay_update():
    payload = valid_homestay()
    payload["is_active"] = False

    data = HomestayUpdateRequest(**payload)

    assert data.is_active is False


def test_update_rejects_owner_id():
    payload = valid_homestay()
    payload["owner_id"] = 999

    with pytest.raises(ValidationError):
        HomestayUpdateRequest(**payload)
