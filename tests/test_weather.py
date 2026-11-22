import asyncio
import logging
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.core.weather_config import WeatherSettings
from app.routers.weather import get_weather_service, router
from app.services.weather import WeatherService


TEST_KEY = "weather-test-key-that-must-not-be-logged"
BASE = "/api/v1/weather"
COORDS = {"lat": 6.9271, "lon": 79.8612}
EPOCH = int(datetime(2026, 1, 1, 18, tzinfo=timezone.utc).timestamp())


def weather_record(timestamp=EPOCH, code=800, probability=0.4):
    return {
        "dt": timestamp,
        "main": {
            "temp": 31, "feels_like": 34, "humidity": 75, "temp_min": 27,
            "temp_max": 36,
        },
        "weather": [
            {"id": code, "main": "Example", "description": "example", "icon": "01d"},
        ],
        "wind": {"speed": 12, "deg": 180},
        "sys": {"sunrise": timestamp - 3600, "sunset": timestamp + 3600},
        "pop": probability,
    }


@pytest.fixture
def setup():
    def factory(handler, *, key=TEST_KEY, clock=lambda: 0):
        service = WeatherService(
            WeatherSettings(key, cache_ttl_seconds=10),
            transport_factory=lambda: httpx.MockTransport(handler),
            clock=clock,
        )
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_weather_service] = lambda: service
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://weather.test",
        )
        return client, service

    return factory


def test_current_and_cache_key(setup, caplog):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        assert b"".join([chunk async for chunk in request.stream]) == b""
        assert request.url.params["appid"] == TEST_KEY
        assert request.url.params["units"] == "metric"
        assert request.url.params["lat"] == "6.927"
        return httpx.Response(200, json=weather_record())

    async def run():
        client, _ = setup(handler)
        async with client:
            response = await client.get(f"{BASE}/current", params=COORDS)
            hit = await client.get(
                f"{BASE}/current", params={"lat": 6.9272, "lon": 79.8613},
            )
        assert response.status_code == 200
        body = response.json()
        assert body == hit.json()
        assert body["temperature_c"] == 31 and body["feels_like_c"] == 34
        assert body["humidity_percent"] == 75 and body["condition"]["icon"] == "01d"
        assert body["wind_speed_kmh"] == 43.2 and body["wind_direction_degrees"] == 180
        assert body["observed_at"] == "2026-01-01T23:30:00+05:30"
        assert body["sunrise"] == "2026-01-01T22:30:00+05:30"
        assert body["sunset"] == "2026-01-02T00:30:00+05:30"
        assert body["source"] == "OpenWeather" and body["fetched_at"]
        assert body["stale"] is False

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    assert calls == ["/data/2.5/weather"]
    assert TEST_KEY not in caplog.text
    assert "result=hit" in caplog.text


def test_forecast_grouping_and_recommendations(setup):
    records = [
        weather_record(
            EPOCH + 10800 * index, code=211 if index == 2 else 800,
            probability=0.8 if index == 3 else None,
        )
        for index in range(10)
    ]

    def handler(request):
        assert request.url.path == "/data/2.5/forecast"
        return httpx.Response(200, json={"list": list(reversed(records))})

    async def run():
        client, _ = setup(handler)
        async with client:
            response = await client.get(f"{BASE}/forecast", params=COORDS)
        assert response.status_code == 200
        body = response.json()
        assert body["timezone"] == "Asia/Colombo"
        assert len(body["intervals"]) == 10
        assert body["intervals"][1]["forecast_at"] == "2026-01-02T02:30:00+05:30"
        assert body["intervals"][0]["rain_probability_percent"] is None
        assert body["intervals"][3]["rain_probability_percent"] == 80
        first, full, last = body["days"]
        assert [day["date"] for day in body["days"]] == [
            "2026-01-01", "2026-01-02", "2026-01-03",
        ]
        assert first["partial"] and last["partial"] and not full["partial"]
        assert full["interval_count"] == 8
        assert first["rain_probability_percent"] is None
        assert full["rain_probability_percent"] == 80
        assert (full["min_temperature_c"], full["max_temperature_c"]) == (27, 36)
        assert full["condition"]["code"] == 211
        assert len(full["cautions"]) == 4
        assert any("museum" in item for item in full["recommendations"])
        assert any("eco-tourism" in item for item in full["recommendations"])
        assert "advisory" in body["advisory_notice"]

    asyncio.run(run())


def test_locations_filter_cache_and_input(setup):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.params["q"] == "Nuwara Eliya,LK"
        return httpx.Response(200, json=[
            {"name": "Nuwara Eliya", "state": "Central", "country": "LK",
             "lat": 6.97, "lon": 80.78},
            {"country": "US"},
        ])

    async def run():
        client, _ = setup(handler)
        async with client:
            response = await client.get(
                f"{BASE}/locations", params={"q": " Nuwara  Eliya "},
            )
            hit = await client.get(f"{BASE}/locations", params={"q": "nuwara eliya"})
            bad = await client.get(f"{BASE}/locations", params={"q": "Kandy,US"})
        assert response.status_code == 200 and response.json() == hit.json()
        assert response.json()["locations"] == [
            {"name": "Nuwara Eliya", "region": "Central", "country": "LK",
             "latitude": 6.97, "longitude": 80.78},
        ]
        assert bad.status_code == 422

    asyncio.run(run())
    assert calls == ["/geo/1.0/direct"]


@pytest.mark.parametrize("coords", [
    {"lat": 91, "lon": 79}, {"lat": 6, "lon": -181},
    {"lat": "nan", "lon": 79}, {"lat": 6, "lon": "inf"}, {"lat": 6},
])
def test_coordinate_validation(setup, coords):
    async def run():
        client, _ = setup(lambda request: pytest.fail("Unexpected upstream call"))
        async with client:
            response = await client.get(f"{BASE}/current", params=coords)
            assert response.status_code == 422

    asyncio.run(run())


def test_expiration_stale_and_recovery(setup):
    now = 0
    status = 200
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, json=weather_record())

    async def run():
        nonlocal now, status
        client, _ = setup(handler, clock=lambda: now)
        async with client:
            fresh = await client.get(f"{BASE}/current", params=COORDS)
            now = 10
            status = 500
            stale = await client.get(f"{BASE}/current", params=COORDS)
            again = await client.get(f"{BASE}/current", params=COORDS)
            assert calls == 3
            assert stale.json() == again.json()
            assert stale.json()["stale"] is True
            assert stale.json()["fetched_at"] == fresh.json()["fetched_at"]
            now = 25
            status = 200
            refreshed = await client.get(f"{BASE}/current", params=COORDS)
            assert refreshed.json()["stale"] is False
            now = 1835
            status = 500
            unavailable = await client.get(f"{BASE}/current", params=COORDS)
            assert unavailable.status_code == 503

    asyncio.run(run())
    assert calls == 6


@pytest.mark.parametrize("kind,status,code,calls", [
    ("missing", 503, "weather_not_configured", 0),
    ("authentication", 502, "weather_upstream_authentication", 1),
    ("rate_limit", 503, "weather_upstream_rate_limited", 1),
    ("timeout", 504, "weather_upstream_timeout", 2),
    ("malformed", 502, "weather_invalid_response", 1),
    ("invalid_shape", 502, "weather_invalid_response", 1),
])
def test_upstream_errors(setup, caplog, kind, status, code, calls):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if kind == "timeout":
            raise httpx.ReadTimeout(TEST_KEY, request=request)
        if kind == "authentication":
            return httpx.Response(401, text=TEST_KEY)
        if kind == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "5"})
        if kind == "invalid_shape":
            return httpx.Response(200, json={"bad": TEST_KEY})
        return httpx.Response(200, text=TEST_KEY)

    async def run():
        client, _ = setup(handler, key="" if kind == "missing" else TEST_KEY)
        async with client:
            response = await client.get(f"{BASE}/current", params=COORDS)
            if kind == "rate_limit":
                assert response.headers["Retry-After"] == "5"
                blocked = await client.get(f"{BASE}/forecast", params=COORDS)
                assert blocked.status_code == 503
        assert response.status_code == status
        assert response.json() == {"detail": {"code": code}}

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    assert count == calls
    assert TEST_KEY not in caplog.text


def test_concurrent_cache_requests_and_local_quota(setup):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return httpx.Response(200, json=weather_record())

    async def run():
        client, service = setup(handler)
        service.quota.max_requests = 1
        async with client:
            responses = await asyncio.gather(*[
                client.get(f"{BASE}/current", params=COORDS) for _ in range(12)
            ])
            assert all(response.status_code == 200 for response in responses)
            assert all(response.json() == responses[0].json() for response in responses)
            blocked = await client.get(f"{BASE}/forecast", params=COORDS)
            assert blocked.status_code == 429
            assert blocked.json()["detail"]["code"] == "weather_quota_exceeded"
            assert int(blocked.headers["Retry-After"]) > 0

    asyncio.run(run())
    assert calls == 1
