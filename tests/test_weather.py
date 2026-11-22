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


@pytest.mark.parametrize("path,params,success_model", [
    ("/current", COORDS, "CurrentWeather"),
    ("/forecast", COORDS, "Forecast"),
    ("/locations", {"q": "Colombo"}, "Locations"),
])
def test_mounted_weather_openapi_matches_runtime(
    path, params, success_model, monkeypatch,
):
    import dotenv
    import pyodbc

    def reject_database_connection(*args, **kwargs):
        pytest.fail("Unexpected database connection")

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(pyodbc, "connect", reject_database_connection)
    from app.main import app

    responses = app.openapi()["paths"][BASE + path]["get"]["responses"]
    assert set(responses) == {"200", "422", "429", "502", "503", "504"}
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{success_model}",
    }
    cases = [
        ("quota", 429, "weather_quota_exceeded", True),
        ("auth", 502, "weather_upstream_authentication", False),
        ("upstream_error", 502, "weather_upstream_error", False),
        ("invalid", 502, "weather_invalid_response", False),
        ("missing", 503, "weather_not_configured", False),
        ("unavailable", 503, "weather_upstream_unavailable", False),
        ("upstream_quota", 503, "weather_upstream_rate_limited", True),
        ("busy", 503, "weather_busy", True),
        ("timeout", 504, "weather_upstream_timeout", False),
    ]
    for status_code in {case[1] for case in cases}:
        documented = responses[str(status_code)]
        schema = documented["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert schema["required"] == ["detail"]
        detail = schema["properties"]["detail"]
        assert detail["type"] == "object"
        assert detail["required"] == ["code"]
        assert detail["properties"]["code"]["type"] == "string"
        assert set(detail["properties"]["code"]["enum"]) == {
            code for _, status, code, _ in cases if status == status_code
        }
        headers = documented.get("headers", {})
        assert set(headers) == ({"Retry-After"} if status_code in (429, 503) else set())
        if headers:
            assert headers["Retry-After"]["schema"] == {"type": "integer", "minimum": 1}
    retry_description = responses["503"]["headers"]["Retry-After"]["description"]
    assert "only" in retry_description
    assert all(code in retry_description for _, status, code, retry in cases
               if status == 503 and retry)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://weather.test",
        ) as client:
            for case, status, code, retry in cases:
                response = await request_mocked_weather(client, app, path, params, case)
                assert response.status_code == status
                assert response.json() == {"detail": {"code": code}}
                if retry:
                    assert int(response.headers["Retry-After"]) >= 1
                else:
                    assert "Retry-After" not in response.headers

    asyncio.run(run())


async def request_mocked_weather(client, app, path, params, case):
    def provider(request):
        if case == "timeout":
            raise httpx.ReadTimeout("Mock timeout", request=request)
        if case == "invalid":
            return httpx.Response(200, text="Invalid JSON")
        if case == "upstream_quota":
            return httpx.Response(429, headers={"Retry-After": "5"})
        status = {"auth": 403, "upstream_error": 404, "unavailable": 500}.get(case)
        assert status is not None, "Unexpected upstream call"
        return httpx.Response(status)

    service = WeatherService(
        WeatherSettings("" if case == "missing" else TEST_KEY),
        transport_factory=lambda: httpx.MockTransport(provider),
    )
    if case == "quota":
        service.quota.max_requests = 1
        service.quota.check_identifier("openweather")
    # Fill the in-flight cache slots without issuing any provider requests.
    pending = []
    if case == "busy":
        pending = [asyncio.create_task(asyncio.sleep(60)) for _ in range(32)]
        service.cache.pending.update({
            ("busy", i): task for i, task in enumerate(pending)
        })
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(app.dependency_overrides, get_weather_service, lambda: service)
        try:
            return await client.get(BASE + path, params=params)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def test_slow_provider_is_cancelled_at_each_attempt_deadline(setup, monkeypatch):
    from types import SimpleNamespace

    from app.services import weather

    deadlines = []
    delays = []
    cancelled = []
    started = asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.append(request.url.path)

    async def expire_deadline(awaitable, timeout):
        deadlines.append(timeout)
        started.clear()
        task = asyncio.create_task(awaitable)
        await started.wait()
        # Advance the deadline immediately after the transport starts blocking.
        return await asyncio.wait_for(task, timeout=0)

    async def record_delay(seconds):
        delays.append(seconds)

    monkeypatch.setattr(
        weather, "asyncio",
        SimpleNamespace(wait_for=expire_deadline, sleep=record_delay),
    )

    async def run():
        client, service = setup(handler)
        async with client:
            response = await client.get(f"{BASE}/current", params=COORDS)
        assert response.status_code == 504
        assert response.json() == {"detail": {"code": "weather_upstream_timeout"}}
        assert "Retry-After" not in response.headers
        assert not service.cache.pending

    asyncio.run(run())
    assert deadlines == [4, 4]
    assert delays == [0.2]
    assert cancelled == ["/data/2.5/weather"] * 2


def test_provider_backoff_expires_across_endpoints(setup):
    now = 0
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "8"})
        return httpx.Response(200, json=[])

    async def run():
        nonlocal now
        client, _ = setup(handler, clock=lambda: now)
        async with client:
            throttled = await client.get(f"{BASE}/current", params=COORDS)
            assert throttled.headers["Retry-After"] == "8"
            now = 6.5
            blocked = await client.get(f"{BASE}/locations", params={"q": "Colombo"})
            assert blocked.headers["Retry-After"] == "2"
            for response in (throttled, blocked):
                assert response.status_code == 503
                assert response.json() == {
                    "detail": {"code": "weather_upstream_rate_limited"},
                }
            assert calls == ["/data/2.5/weather"]
            now = 8
            recovered = await client.get(f"{BASE}/locations", params={"q": "Colombo"})
            assert recovered.status_code == 200
            assert recovered.json()["stale"] is False
            assert "Retry-After" not in recovered.headers

    asyncio.run(run())
    assert calls == ["/data/2.5/weather", "/geo/1.0/direct"]


def test_real_inflight_saturation_coalesces_and_serves_stale(setup):
    async def run():
        now = 0
        calls = []
        full = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            calls.append(request.url.params["lat"])
            if len(calls) == 33:
                full.set()
            if len(calls) > 1:
                await release.wait()
            return httpx.Response(200, json=weather_record())

        client, service = setup(handler, clock=lambda: now)
        # Exercise the 32-key in-flight boundary independently of the 20-call quota.
        service.quota.max_requests = 100
        async with client:
            def current(lat):
                return client.get(f"{BASE}/current", params={"lat": lat, "lon": 80})

            prime = await current(0)
            assert prime.status_code == 200
            now = 10
            requests = [asyncio.create_task(current(lat)) for lat in range(1, 33)]
            try:
                await full.wait()
                assert len(service.cache.pending) == 32
                same_key = asyncio.create_task(current(1))
                requests.append(same_key)
                stale = await current(0)
                busy = await current(33)
                assert stale.status_code == 200 and stale.json()["stale"] is True
                assert stale.json()["fetched_at"] == prime.json()["fetched_at"]
                assert busy.status_code == 503
                assert busy.json() == {"detail": {"code": "weather_busy"}}
                assert busy.headers["Retry-After"] == "1"
                assert len(calls) == 33
                assert not same_key.done()
            finally:
                release.set()
                results = await asyncio.gather(*requests)
            assert all(response.status_code == 200 for response in results)
            assert results[0].json() == results[-1].json()
            assert not service.cache.pending
            assert len(calls) == 33
            assert (await current(33)).status_code == 200
            assert len(calls) == 34

    asyncio.run(run())


def test_quota_is_per_service_and_recovers_at_window_boundary(setup, monkeypatch):
    from types import SimpleNamespace

    from app.core import rate_limiter

    now = 100.0
    calls = []
    monkeypatch.setattr(rate_limiter, "time", SimpleNamespace(time=lambda: now))

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=weather_record())

    async def run():
        nonlocal now
        first, first_service = setup(handler)
        second, second_service = setup(handler)
        first_service.quota.max_requests = second_service.quota.max_requests = 1
        async with first, second:
            async def current(client, lat):
                return await client.get(
                    f"{BASE}/current", params={"lat": lat, "lon": 80},
                )

            assert (await current(first, 1)).status_code == 200
            blocked = await current(first, 2)
            assert blocked.status_code == 429
            assert blocked.json() == {"detail": {"code": "weather_quota_exceeded"}}
            assert blocked.headers["Retry-After"] == "60"
            assert (await current(second, 2)).status_code == 200
            now = 159.0
            assert (await current(first, 2)).headers["Retry-After"] == "1"
            now = 160.0
            recovered = await current(first, 2)
            assert recovered.status_code == 200
            assert "Retry-After" not in recovered.headers

    asyncio.run(run())
    assert len(calls) == 3


@pytest.mark.parametrize(
    "failure", ["oversized_body", "empty_forecast", "duplicate_slot"],
)
def test_invalid_forecast_uses_stale_until_exact_expiry(setup, caplog, failure):
    now = 0
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1 or now == 1811:
            return httpx.Response(200, json={"list": [weather_record()]})
        if failure == "oversized_body":
            return httpx.Response(200, content=TEST_KEY.encode() + b"x" * (256 * 1024))
        if failure == "empty_forecast":
            return httpx.Response(200, json={"list": []})
        return httpx.Response(200, json={"list": [weather_record(), weather_record()]})

    async def run():
        nonlocal now
        client, _ = setup(handler, clock=lambda: now)
        async with client:
            fresh = await client.get(f"{BASE}/forecast", params=COORDS)
            assert fresh.status_code == 200
            now = 10
            stale = await client.get(f"{BASE}/forecast", params=COORDS)
            assert stale.status_code == 200
            assert stale.json() == {**fresh.json(), "stale": True}
            now = 24
            assert (
                await client.get(f"{BASE}/forecast", params=COORDS)
            ).json() == stale.json()
            assert calls == 2
            now = 1810
            expired = await client.get(f"{BASE}/forecast", params=COORDS)
            assert expired.status_code == 502
            assert expired.json() == {"detail": {"code": "weather_invalid_response"}}
            assert "Retry-After" not in expired.headers
            now = 1811
            recovered = await client.get(f"{BASE}/forecast", params=COORDS)
            assert recovered.status_code == 200 and recovered.json()["stale"] is False

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    assert calls == 4
    assert TEST_KEY not in caplog.text


@pytest.mark.parametrize("start,first,last,counts", [
    (
        datetime(2026, 1, 1, 0, tzinfo=timezone.utc),
        "2026-01-01T05:30:00+05:30",
        "2026-01-06T02:30:00+05:30",
        [7, 8, 8, 8, 8, 1],
    ),
    (
        datetime(2026, 1, 1, 21, tzinfo=timezone.utc),
        "2026-01-02T02:30:00+05:30",
        "2026-01-06T23:30:00+05:30",
        [8, 8, 8, 8, 8],
    ),
])
def test_complete_provider_forecast_groups_local_calendar_days(
    setup, start, first, last, counts,
):
    records = [weather_record(int(start.timestamp()) + 10800 * i) for i in range(40)]

    def handler(request):
        assert request.url.path == "/data/2.5/forecast"
        assert request.url.params["units"] == "metric"
        return httpx.Response(200, json={"cnt": 40, "list": records})

    async def run():
        client, _ = setup(handler)
        async with client:
            response = await client.get(f"{BASE}/forecast", params=COORDS)
        assert response.status_code == 200
        body = response.json()
        assert body["timezone"] == "Asia/Colombo"
        times = [item["forecast_at"] for item in body["intervals"]]
        assert len(times) == 40
        assert (times[0], times[-1]) == (first, last)
        for timestamp, record in zip(times, records, strict=True):
            assert timestamp.endswith("+05:30")
            assert int(datetime.fromisoformat(timestamp).timestamp()) == record["dt"]
        days = body["days"]
        assert [day["interval_count"] for day in days] == counts
        assert [day["partial"] for day in days] == [count < 8 for count in counts]
        assert [day["date"] for day in days] == list(
            dict.fromkeys(t[:10] for t in times)
        )

    asyncio.run(run())


def test_out_of_order_forecast_preserves_missing_slot(setup):
    # Omit the 11:30 local slot from one otherwise complete local calendar day.
    first = EPOCH + 10800
    offsets = [7, 1, 0, 6, 2, 5, 4]
    records = [weather_record(first + 10800 * offset) for offset in offsets]

    async def run():
        client, _ = setup(lambda request: httpx.Response(200, json={"list": records}))
        async with client:
            response = await client.get(f"{BASE}/forecast", params=COORDS)
        assert response.status_code == 200
        body = response.json()
        timestamps = [item["forecast_at"] for item in body["intervals"]]
        assert [timestamp[11:16] for timestamp in timestamps] == [
            "02:30", "05:30", "08:30", "14:30", "17:30", "20:30", "23:30",
        ]
        day, = body["days"]
        assert day["date"] == "2026-01-02"
        assert (day["interval_count"], day["partial"]) == (7, True)

    asyncio.run(run())


@pytest.mark.parametrize("second_offset", [-1, 0])
def test_current_provider_field_mapping_at_local_midnight(setup, second_offset):
    midnight = int(datetime(2026, 1, 1, 18, 30, tzinfo=timezone.utc).timestamp())
    record = weather_record(midnight + second_offset)
    record["main"].update(temp=28.25, feels_like=32.5, humidity=83)
    record["wind"] = {"speed": 2.75, "deg": 245}
    record["weather"] = [
        {"id": 500, "main": "Rain", "description": "light rain", "icon": "10n"},
    ]
    record["sys"] = {"sunrise": midnight + 6 * 3600, "sunset": midnight + 18 * 3600}

    async def run():
        client, _ = setup(lambda request: httpx.Response(200, json=record))
        async with client:
            response = await client.get(f"{BASE}/current", params=COORDS)
        assert response.status_code == 200
        body = response.json()
        assert body["observed_at"] == (
            "2026-01-01T23:59:59+05:30" if second_offset == -1
            else "2026-01-02T00:00:00+05:30"
        )
        assert body["sunrise"] == "2026-01-02T06:00:00+05:30"
        assert body["sunset"] == "2026-01-02T18:00:00+05:30"
        assert body["temperature_c"] == 28.25
        assert body["feels_like_c"] == 32.5
        assert body["humidity_percent"] == 83
        assert body["wind_speed_kmh"] == 9.9
        assert body["wind_direction_degrees"] == 245
        assert body["condition"] == {
            "code": 500, "name": "Rain", "description": "light rain", "icon": "10n",
        }

    asyncio.run(run())


@pytest.mark.parametrize("rain,wind,heat,code,caution,specific_advice", [
    (59, 10, 30, 800, None, None),
    (60, 10, 30, 800, "Rain", "rain gear"),
    (0, 39.9, 30, 800, None, None),
    (0, 40, 30, 800, "Strong winds", None),
    (0, 10, 34.9, 800, None, None),
    (0, 10, 35, 800, "High temperatures", "early morning"),
    (0, 10, 30, 211, "Thunderstorms", None),
])
def test_forecast_advice_thresholds_and_day_assignment(
    setup, rain, wind, heat, code, caution, specific_advice,
):
    clear = weather_record(EPOCH, probability=0)
    clear["main"].update(temp=28, temp_min=25, temp_max=30)
    clear["wind"]["speed"] = 2.5
    target = weather_record(EPOCH + 10800, code=code, probability=rain / 100)
    target["main"].update(temp=29.25, temp_min=26, temp_max=heat)
    target["wind"]["speed"] = wind / 3.6
    target["weather"][0].update(
        main="Thunderstorm" if code == 211 else "Clear",
        description="thunderstorm" if code == 211 else "clear sky",
        icon="11n" if code == 211 else "01n",
    )

    async def run():
        client, _ = setup(
            lambda request: httpx.Response(200, json={"list": [clear, target]}),
        )
        async with client:
            response = await client.get(f"{BASE}/forecast", params=COORDS)
        assert response.status_code == 200
        body = response.json()
        previous, day = body["days"]
        assert previous["date"] == "2026-01-01"
        assert previous["cautions"] == []
        assert any("outdoor outing" in item for item in previous["recommendations"])
        assert not any(
            "indoor workshop" in item for item in previous["recommendations"]
        )
        assert day["date"] == "2026-01-02"
        interval = body["intervals"][1]
        assert interval["forecast_at"] == "2026-01-02T02:30:00+05:30"
        assert (
            interval["rain_probability_percent"] == day["rain_probability_percent"]
            == rain
        )
        assert interval["wind_speed_kmh"] == wind
        assert interval["temperature_c"] == 29.25
        assert interval["min_temperature_c"] == day["min_temperature_c"] == 26
        assert interval["max_temperature_c"] == day["max_temperature_c"] == heat
        expected_condition = {
            "code": code,
            "name": "Thunderstorm" if code == 211 else "Clear",
            "description": "thunderstorm" if code == 211 else "clear sky",
            "icon": "11n" if code == 211 else "01n",
        }
        assert interval["condition"] == day["condition"] == expected_condition
        if caution is None:
            assert day["cautions"] == []
            assert any("outdoor outing" in item for item in day["recommendations"])
        else:
            assert len(day["cautions"]) == 1
            assert day["cautions"][0].startswith(caution)
            assert any("indoor workshop" in item for item in day["recommendations"])
        if specific_advice:
            assert any(specific_advice in item for item in day["recommendations"])
            assert all(
                specific_advice not in item for item in previous["recommendations"]
            )

    asyncio.run(run())


def test_malformed_forecast_shape_uses_eligible_stale_data(setup):
    now = 0

    def handler(request):
        payload = {"list": [weather_record()]} if now == 0 else {"list": {"dt": EPOCH}}
        return httpx.Response(200, json=payload)

    async def run():
        nonlocal now
        client, _ = setup(handler, clock=lambda: now)
        async with client:
            fresh = await client.get(f"{BASE}/forecast", params=COORDS)
            assert fresh.status_code == 200
            now = 10
            stale = await client.get(f"{BASE}/forecast", params=COORDS)
            assert stale.status_code == 200
            assert stale.json() == {**fresh.json(), "stale": True}
            now = 1810
            expired = await client.get(f"{BASE}/forecast", params=COORDS)
            assert expired.status_code == 502
            assert expired.json() == {"detail": {"code": "weather_invalid_response"}}
            assert "Retry-After" not in expired.headers

    asyncio.run(run())


@pytest.mark.parametrize("bounds,interval_bounds,daily_bounds", [
    ({"temp_max": 37}, (31, 37), (28, 37)),
    ({"temp_min": 25}, (25, 31), (25, 34)),
    ({}, (31, 31), (28, 34)),
    ({"temp_min": 25, "temp_max": 37}, (25, 37), (25, 37)),
], ids=["missing-min", "missing-max", "missing-both", "explicit-bounds"])
def test_forecast_temperature_bound_fallback(
    setup, bounds, interval_bounds, daily_bounds,
):
    first = weather_record(EPOCH + 10800)
    second = weather_record(EPOCH + 21600)
    first["main"] = {"temp": 31, **bounds}
    second["main"].update(temp_min=28, temp_max=34)

    async def run():
        client, _ = setup(
            lambda request: httpx.Response(200, json={"list": [first, second]}),
        )
        async with client:
            response = await client.get(f"{BASE}/forecast", params=COORDS)
        assert response.status_code == 200
        body = response.json()
        interval = body["intervals"][0]
        assert interval["temperature_c"] == 31
        assert (interval["min_temperature_c"], interval["max_temperature_c"]) == (
            interval_bounds
        )
        day, = body["days"]
        assert day["interval_count"] == 2
        assert (day["min_temperature_c"], day["max_temperature_c"]) == daily_bounds

    asyncio.run(run())


@pytest.mark.parametrize("temperatures", [
    {"temp": 31, "temp_min": "invalid"},
    {"temp": 31, "temp_max": None},
    {"temp_min": 25, "temp_max": 37},
    {"temp": "invalid"},
    {"temp": "NaN"},
], ids=["invalid-min", "null-max", "missing-temp", "invalid-temp", "nonfinite-temp"])
def test_forecast_temperature_fallback_preserves_validation(setup, temperatures):
    record = weather_record()
    record["main"] = temperatures

    async def run():
        client, _ = setup(
            lambda request: httpx.Response(200, json={"list": [record]}),
        )
        async with client:
            response = await client.get(f"{BASE}/forecast", params=COORDS)
        assert response.status_code == 502
        assert response.json() == {"detail": {"code": "weather_invalid_response"}}
        assert "Retry-After" not in response.headers

    asyncio.run(run())
