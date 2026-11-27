import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.weather_config import SRI_LANKA_TIME, WeatherSettings
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import (
    MVPListing,
    MVPListingMedia,
    MVPListingVerification,
    MVPProviderProfile,
)
from app.routers.weather import get_weather_service
from app.services import mvp_planner_weather
from app.services.weather import WeatherService
from tests.test_mvp_listing_management import managed  # noqa: F401
from tests.test_weather import EPOCH, TEST_KEY, weather_record

BASE = "/api/v1"
LOCATIONS = [
    {"country": "US", "name": "Example", "lat": 40, "lon": -75},
    {"country": "LK", "name": "Colombo", "lat": 6.9271, "lon": 79.8612},
]


def test_mvp_guest_weather_contract():
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if "/geo/" in request.url.path:
            return httpx.Response(
                200, json=LOCATIONS if "Colombo" in request.url.params["q"] else []
            )
        if request.url.path.endswith("/forecast"):
            return httpx.Response(
                200,
                json={
                    "list": [
                        weather_record(EPOCH + step * 10800) for step in range(40)
                    ],
                },
            )
        return httpx.Response(200, json=weather_record())

    service = WeatherService(
        WeatherSettings(TEST_KEY),
        transport_factory=lambda: httpx.MockTransport(provider),
    )
    app = create_mvp_app()
    app.dependency_overrides[get_weather_service] = lambda: service
    with TestClient(app) as client:
        base = BASE + "/weather"
        search = client.get(base + "/search", params={"q": " Colombo "})
        assert search.status_code == 200
        assert (
            search.json()
            == client.get(base + "/locations", params={"q": "Colombo"}).json()
        )
        assert [place["name"] for place in search.json()["locations"]] == ["Colombo"]
        current = client.get(base + "/current", params={"location": "Colombo"}).json()
        assert current["temperature_c"] == 31 and current["observed_at"].endswith(
            "+05:30"
        )
        forecast = client.get(base + "/forecast", params={"location": "Colombo"}).json()
        assert len(forecast["daily_forecast"]) == 5 and len(forecast["intervals"]) == 40
        assert forecast["daily_forecast"] == forecast["days"][:5]
        assert (
            client.get(base + "/current", params={"location": "Unknown"}).status_code
            == 404
        )
        before = len(calls)
        for params in (
            {"lat": 6},
            {"lat": 91, "lon": 80},
            {"lat": 7, "lon": 80, "location": "Colombo"},
        ):
            assert client.get(base + "/current", params=params).status_code == 422
        assert len(calls) == before
        paths = client.get("/openapi.json").json()["paths"]
        assert all(
            BASE + path in paths
            for path in (
                "/bookings/{booking_id}/status",
                "/payments/at-location",
                "/provider/wallet",
                "/map/markers",
            )
        )


def test_map_filters_visibility_coordinates_and_cover(managed):  # noqa: F811
    client, _, _ = managed
    dependency = client.app.dependency_overrides[get_mvp_db]()
    db = next(dependency)
    try:
        profiles = db.scalars(
            select(MVPProviderProfile).order_by(MVPProviderProfile.id)
        ).all()
        profiles[1].verification_status = "suspended"

        def listing(
            title,
            *,
            category="adventure",
            district="Badulla",
            lat=6.9,
            lon=81,
            approval="approved",
            active=True,
            provider=0,
        ):
            row = MVPListing(
                title=title,
                description="private detail",
                district=district,
                locality="Ella",
                category=category,
                latitude=lat,
                longitude=lon,
                approval_status=approval,
                is_active=active,
                price=100,
                provider_id=profiles[provider].id,
            )
            db.add(row)
            db.flush()
            return row.id

        hike = listing("Ella hike")
        artisan = listing("Woodcraft", category="artisan", district="Kandy", lon=80)
        listing("Missing", lat=None)
        listing("Invalid", lon=181)
        listing("Placeholder", lat=0, lon=0)
        listing("Unpublished", active=False)
        listing("Draft", approval="draft", active=False)
        listing("Suspended provider", provider=1)
        db.add_all(
            [
                MVPListingMedia(
                    listing_id=hike,
                    sort_order=0,
                    storage_key="https://example.org/fallback.png",
                ),
                MVPListingMedia(
                    listing_id=hike,
                    sort_order=1,
                    storage_key="https://example.org/hidden.png",
                    is_visible=False,
                ),
                MVPListingMedia(
                    listing_id=hike,
                    sort_order=2,
                    storage_key="https://example.org/cover.png",
                ),
            ]
        )
        db.flush()
        photos = db.scalars(
            select(MVPListingMedia).order_by(MVPListingMedia.sort_order)
        ).all()
        db.add(
            MVPListingVerification(
                listing_id=hike,
                details=json.dumps(
                    {
                        "cover_photo_id": photos[2].id,
                    }
                ),
            )
        )
        db.commit()
    finally:
        dependency.close()
    url = BASE + "/map/markers"
    rows = client.get(url).json()
    assert {item["listing_id"] for item in rows} == {hike, artisan}
    (item,) = client.get(
        url,
        params={
            "category": "adventure",
            "district": " badulla ",
            "q": "HIKE",
            "bbox": "80,6,82,8",
        },
    ).json()
    assert item["listing_id"] == hike and item["pricing_unit"] == "per_participant"
    assert item["detail_url"] == f"{BASE}/activities/{hike}"
    assert item["cover_image_url"] == "https://example.org/cover.png"
    assert "provider_id" not in item and "address" not in item
    (artisan_item,) = client.get(url, params={"category": "artisan"}).json()
    assert artisan_item["display_price"] is None
    assert client.get(url, params={"q": "hike%other"}).json() == []
    assert (
        client.get(url, params={"bbox": "79,6,80,7"}).json()[0]["listing_id"] == artisan
    )
    for params in (
        {"limit": 0},
        {"bbox": "80,9,79,8"},
        {"bbox": "NaN,5,80,7"},
        {"q": "   "},
    ):
        assert client.get(url, params=params).status_code == 422


def test_planner_only_attaches_real_forecast_dates(monkeypatch):
    today = datetime.now(SRI_LANKA_TIME).replace(
        hour=2, minute=30, second=0, microsecond=0
    )

    def provider(request):
        if "/geo/" in request.url.path:
            return httpx.Response(200, json=LOCATIONS)
        return httpx.Response(
            200, json={"list": [weather_record(int(today.timestamp()))]}
        )

    service = WeatherService(
        WeatherSettings(TEST_KEY),
        transport_factory=lambda: httpx.MockTransport(provider),
    )
    monkeypatch.setattr(mvp_planner_weather, "get_weather_service", lambda: service)
    plan = SimpleNamespace(
        start_date=today.date(),
        days=[SimpleNamespace(day=day, destination="Colombo") for day in (1, 2, 12)],
    )
    weather = asyncio.run(mvp_planner_weather.itinerary_weather(plan))
    assert [day.day for day in weather] == [1]
    assert weather[0].forecast.date == today.date()
    assert weather[0].source == "OpenWeather"
    plan.start_date = today.date() + timedelta(days=10)
    assert asyncio.run(mvp_planner_weather.itinerary_weather(plan)) == []
