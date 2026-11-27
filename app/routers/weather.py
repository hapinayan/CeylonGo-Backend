import unicodedata
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.weather_cache import WeatherError
from app.core.weather_config import WeatherSettings
from app.schemas.weather import CurrentWeather, Forecast, Locations
from app.services.weather import WeatherService


def _weather_error_response(description: str, *codes: str, retry_after: str = ""):
    response = {
        "description": description,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["detail"],
                    "properties": {
                        "detail": {
                            "type": "object",
                            "required": ["code"],
                            "properties": {
                                "code": {"type": "string", "enum": list(codes)},
                            },
                        },
                    },
                },
            },
        },
    }
    if retry_after:
        response["headers"] = {
            "Retry-After": {
                "description": retry_after,
                "schema": {"type": "integer", "minimum": 1},
            },
        }
    return response


WEATHER_ERROR_RESPONSES = {
    429: _weather_error_response(
        "Local weather quota exceeded.",
        "weather_quota_exceeded",
        retry_after="Seconds until the local quota permits another upstream request.",
    ),
    502: _weather_error_response(
        "The upstream provider rejected the request or returned an invalid response.",
        "weather_upstream_authentication",
        "weather_upstream_error",
        "weather_invalid_response",
    ),
    503: _weather_error_response(
        "Weather is unconfigured, unavailable, throttled upstream, or locally busy.",
        "weather_not_configured",
        "weather_upstream_unavailable",
        "weather_upstream_rate_limited",
        "weather_busy",
        retry_after=(
            "Seconds to wait. Present only for weather_upstream_rate_limited "
            "and weather_busy."
        ),
    ),
    504: _weather_error_response(
        "The upstream weather request timed out.",
        "weather_upstream_timeout",
    ),
}


router = APIRouter(
    prefix="/api/v1/weather", tags=["Weather"], responses=WEATHER_ERROR_RESPONSES,
)


@lru_cache(maxsize=1)
def get_weather_service() -> WeatherService:
    try:
        return WeatherService(WeatherSettings.from_env())
    except ValueError:
        raise HTTPException(
            status_code=503, detail={"code": "weather_not_configured"},
        ) from None


Latitude = Annotated[float | None, Query(ge=-90, le=90)]
Longitude = Annotated[float | None, Query(ge=-180, le=180)]
Service = Annotated[WeatherService, Depends(get_weather_service)]


async def weather_response(result):
    try:
        return await result
    except WeatherError as error:
        headers = (
            {"Retry-After": str(error.retry_after)}
            if error.retry_after is not None else None
        )
        raise HTTPException(
            status_code=error.status, detail={"code": error.code}, headers=headers,
        ) from None


def normalize_location_query(q: str) -> str:
    query = " ".join(unicodedata.normalize("NFC", q).split())
    if (
        not 2 <= len(query) <= 80
        or not any(char.isalpha() for char in query)
        or any(
            char not in " .-'\u2019"
            and unicodedata.category(char)[0] not in ("L", "M", "N")
            for char in query
        )
    ):
        raise HTTPException(status_code=422, detail={"code": "invalid_location_query"})
    return query


async def resolve_coordinates(service: WeatherService, lat, lon, location):
    if location is None and lat is not None and lon is not None:
        return lat, lon
    if location is None or lat is not None or lon is not None:
        raise HTTPException(422, detail={"code": "provide_coordinates_or_location"})
    query = normalize_location_query(location)
    matches = await weather_response(service.locations(query))
    if not matches.locations:
        raise HTTPException(404, detail={"code": "weather_location_not_found"})
    match = matches.locations[0]
    return match.latitude, match.longitude


@router.get("/current", response_model=CurrentWeather)
async def current_weather(
    service: Service, lat: Latitude = None, lon: Longitude = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
):
    lat, lon = await resolve_coordinates(service, lat, lon, location)
    return await weather_response(service.conditions("current", lat, lon))


@router.get("/forecast", response_model=Forecast)
async def weather_forecast(
    service: Service, lat: Latitude = None, lon: Longitude = None,
    location: Annotated[str | None, Query(max_length=120)] = None,
):
    lat, lon = await resolve_coordinates(service, lat, lon, location)
    return await weather_response(service.conditions("forecast", lat, lon))


@router.get("/search", response_model=Locations)
@router.get("/locations", response_model=Locations)
async def search_locations(q: Annotated[str, Query(max_length=120)], service: Service):
    return await weather_response(service.locations(normalize_location_query(q)))
