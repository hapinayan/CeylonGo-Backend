import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

import httpx
from fastapi import HTTPException

from app.core.rate_limiter import SlidingWindowRateLimiter
from app.core.weather_cache import WeatherCache, WeatherError
from app.core.weather_config import SRI_LANKA_TIME, WeatherSettings
from app.schemas.weather import (
    Condition,
    Coordinates,
    CurrentWeather,
    DailyForecast,
    Forecast,
    ForecastInterval,
    Location,
    Locations,
    WeatherMetadata,
)
from app.services.weather_recommendations import travel_advice


logger = logging.getLogger(__name__)
PATHS = {
    "current": "/data/2.5/weather",
    "forecast": "/data/2.5/forecast",
    "locations": "/geo/1.0/direct",
}


class OpenWeatherTransport(httpx.AsyncBaseTransport):
    """Add provider query parameters below HTTPX's URL logging layer."""

    def __init__(self, transport: httpx.AsyncBaseTransport, params: dict):
        self.transport = transport
        self.params = params

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        upstream = httpx.Request(
            request.method, request.url, params=self.params,
            headers=request.headers, extensions=request.extensions,
        )
        return await self.transport.handle_async_request(upstream)

    async def aclose(self) -> None:
        await self.transport.aclose()


def local_time(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, SRI_LANKA_TIME)


def condition(data: dict) -> Condition:
    weather = data["weather"][0]
    return Condition(
        code=weather["id"], name=weather["main"],
        description=weather["description"], icon=weather["icon"],
    )


def normalize_current(data: dict, coords: Coordinates, fetched: datetime):
    return CurrentWeather(
        coordinates=coords, fetched_at=fetched,
        temperature_c=data["main"]["temp"], feels_like_c=data["main"]["feels_like"],
        humidity_percent=data["main"]["humidity"], condition=condition(data),
        wind_speed_kmh=round(data["wind"]["speed"] * 3.6, 2),
        wind_direction_degrees=data["wind"].get("deg"),
        sunrise=local_time(data["sys"]["sunrise"]),
        sunset=local_time(data["sys"]["sunset"]),
        observed_at=local_time(data["dt"]),
    )


def normalize_interval(item: dict) -> ForecastInterval:
    probability = item.get("pop")
    temperatures = item["main"]
    temperature = temperatures["temp"]
    return ForecastInterval(
        forecast_at=local_time(item["dt"]), temperature_c=temperature,
        min_temperature_c=temperatures.get("temp_min", temperature),
        max_temperature_c=temperatures.get("temp_max", temperature),
        rain_probability_percent=(
            None if probability is None else round(probability * 100, 2)
        ),
        wind_speed_kmh=round(item["wind"]["speed"] * 3.6, 2),
        condition=condition(item),
    )


def summarize_day(intervals: list[ForecastInterval]) -> DailyForecast:
    recommendations, cautions = travel_advice(intervals)
    wet = [item for item in intervals if item.condition.code // 100 in {2, 3, 5}]
    storms = [item for item in wet if item.condition.code // 100 == 2]
    representative = min(
        storms or wet or intervals,
        key=lambda item: abs(
            item.forecast_at.hour * 60 + item.forecast_at.minute - 720
        ),
    )
    return DailyForecast(
        date=intervals[0].forecast_at.date(), interval_count=len(intervals),
        partial=len(intervals) < 8,
        min_temperature_c=min(item.min_temperature_c for item in intervals),
        max_temperature_c=max(item.max_temperature_c for item in intervals),
        rain_probability_percent=max(
            (
                item.rain_probability_percent for item in intervals
                if item.rain_probability_percent is not None
            ),
            default=None,
        ),
        condition=representative.condition,
        recommendations=recommendations, cautions=cautions,
    )


def normalize_forecast(data: dict, coords: Coordinates, fetched: datetime) -> Forecast:
    raw = data["list"]
    if not isinstance(raw, list) or len(raw) > 40:
        raise ValueError("Unexpected interval count")
    intervals = sorted(map(normalize_interval, raw), key=lambda item: item.forecast_at)
    if not intervals or any(
        (right.forecast_at - left.forecast_at).total_seconds() < 10800
        for left, right in zip(intervals, intervals[1:])
    ):
        raise ValueError("Invalid forecast intervals")
    grouped = defaultdict(list)
    for interval in intervals:
        grouped[interval.forecast_at.date()].append(interval)
    return Forecast(
        coordinates=coords, fetched_at=fetched, intervals=intervals,
        days=[summarize_day(day) for day in grouped.values()],
    )


def normalize_locations(data: list, fetched: datetime) -> Locations:
    if not isinstance(data, list) or len(data) > 5:
        raise ValueError("Unexpected location results")
    locations = [
        Location(
            name=item["name"], region=item.get("state"),
            latitude=item["lat"], longitude=item["lon"],
        )
        for item in data if item["country"] == "LK"
    ]
    return Locations(fetched_at=fetched, locations=locations)


class WeatherService:
    def __init__(
        self, settings: WeatherSettings, *,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] = (
            httpx.AsyncHTTPTransport
        ),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.transport_factory = transport_factory
        self.cache: WeatherCache[WeatherMetadata] = WeatherCache(
            settings.cache_ttl_seconds, clock=clock,
        )
        self.quota = SlidingWindowRateLimiter(max_requests=20, window_seconds=60)
        self.clock = clock
        self.provider_retry_at = 0.0

    async def conditions(self, endpoint: str, lat: float, lon: float):
        coords = Coordinates(latitude=round(lat, 3) or 0, longitude=round(lon, 3) or 0)
        normalizer = normalize_current if endpoint == "current" else normalize_forecast
        return await self._get(
            endpoint, (coords.latitude, coords.longitude),
            {"lat": coords.latitude, "lon": coords.longitude, "units": "metric"},
            lambda data, fetched: normalizer(data, coords, fetched),
        )

    async def locations(self, query: str):
        return await self._get(
            "locations", (query.casefold(),), {"q": f"{query},LK", "limit": 5},
            normalize_locations,
        )

    async def _get(self, endpoint, key, params, normalize):
        async def load():
            payload = await self._request(endpoint, params)
            try:
                return normalize(payload, datetime.now(timezone.utc))
            except (AttributeError, IndexError, KeyError, OSError, OverflowError,
                    TypeError, ValueError):
                logger.warning("weather upstream endpoint=%s result=invalid", endpoint)
                raise WeatherError(
                    502, "weather_invalid_response", transient=True,
                ) from None

        try:
            if not self.settings.api_key:
                raise WeatherError(503, "weather_not_configured")
            value, outcome = await self.cache.get((endpoint, *key), load)
        except WeatherError as error:
            logger.warning("weather endpoint=%s result=%s", endpoint, error.code)
            raise
        logger.info("weather endpoint=%s result=%s", endpoint, outcome)
        return value

    async def _request(self, endpoint: str, params: dict):
        transport = OpenWeatherTransport(
            self.transport_factory(), {**params, "appid": self.settings.api_key},
        )
        async with httpx.AsyncClient(
            base_url=self.settings.base_url, transport=transport,
            timeout=httpx.Timeout(3.0, connect=2.0), follow_redirects=False,
        ) as client:
            for attempt in range(2):
                self._check_quota()
                try:
                    return await asyncio.wait_for(
                        self._fetch(client, endpoint), timeout=4,
                    )
                except (TimeoutError, httpx.TimeoutException):
                    error = WeatherError(
                        504, "weather_upstream_timeout", transient=True,
                    )
                except (httpx.RequestError, httpx.StreamError):
                    error = WeatherError(
                        503, "weather_upstream_unavailable", transient=True,
                    )
                except WeatherError as upstream_error:
                    if upstream_error.code != "weather_upstream_unavailable":
                        raise
                    error = upstream_error
                logger.warning(
                    "weather upstream endpoint=%s attempt=%d result=%s",
                    endpoint, attempt + 1, error.code,
                )
                if attempt == 0:
                    await asyncio.sleep(0.2)
            raise error

    def _check_quota(self) -> None:
        remaining = self.provider_retry_at - self.clock()
        if remaining > 0:
            raise WeatherError(
                503, "weather_upstream_rate_limited", transient=True,
                retry_after=int(remaining) + 1,
            )
        try:
            self.quota.check_identifier("openweather")
        except HTTPException as error:
            raise WeatherError(
                429, "weather_quota_exceeded", transient=True,
                retry_after=int(error.headers["Retry-After"]),
            ) from None

    async def _fetch(self, client: httpx.AsyncClient, endpoint: str):
        async with client.stream("GET", PATHS[endpoint]) as response:
            status = response.status_code
            logger.info("weather upstream endpoint=%s status=%d", endpoint, status)
            if status in (401, 403):
                raise WeatherError(502, "weather_upstream_authentication")
            if status == 429:
                header = response.headers.get("Retry-After", "")
                delay = min(3600, max(1, int(header))) if header.isdecimal() and (
                    header.isascii() and len(header) <= 10
                ) else 60
                self.provider_retry_at = max(
                    self.provider_retry_at, self.clock() + delay,
                )
                raise WeatherError(
                    503, "weather_upstream_rate_limited", transient=True,
                    retry_after=delay,
                )
            if 500 <= status <= 599:
                raise WeatherError(503, "weather_upstream_unavailable", transient=True)
            if status != 200:
                raise WeatherError(502, "weather_upstream_error")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                body.extend(chunk)
                if len(body) > 256 * 1024:
                    raise WeatherError(502, "weather_invalid_response", transient=True)
            try:
                return json.loads(body)
            except (ValueError, RecursionError):
                raise WeatherError(
                    502, "weather_invalid_response", transient=True,
                ) from None
