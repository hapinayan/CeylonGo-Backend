import unicodedata
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.weather_cache import WeatherError
from app.core.weather_config import WeatherSettings
from app.schemas.weather import CurrentWeather, Forecast, Locations
from app.services.weather import WeatherService


router = APIRouter(prefix="/api/v1/weather", tags=["Weather"])


@lru_cache(maxsize=1)
def get_weather_service() -> WeatherService:
    try:
        return WeatherService(WeatherSettings.from_env())
    except ValueError:
        raise HTTPException(
            status_code=503, detail={"code": "weather_not_configured"},
        ) from None


Latitude = Annotated[float, Query(ge=-90, le=90)]
Longitude = Annotated[float, Query(ge=-180, le=180)]
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


@router.get("/current", response_model=CurrentWeather)
async def current_weather(lat: Latitude, lon: Longitude, service: Service):
    return await weather_response(service.conditions("current", lat, lon))


@router.get("/forecast", response_model=Forecast)
async def weather_forecast(lat: Latitude, lon: Longitude, service: Service):
    return await weather_response(service.conditions("forecast", lat, lon))


@router.get("/locations", response_model=Locations)
async def search_locations(q: Annotated[str, Query(max_length=120)], service: Service):
    query = " ".join(unicodedata.normalize("NFC", q).split())
    if (
        not 2 <= len(query) <= 80
        or not any(char.isalpha() for char in query)
        or any(
            char not in " .-'’"
            and unicodedata.category(char)[0] not in ("L", "M", "N")
            for char in query
        )
    ):
        raise HTTPException(status_code=422, detail={"code": "invalid_location_query"})
    return await weather_response(service.locations(query))
