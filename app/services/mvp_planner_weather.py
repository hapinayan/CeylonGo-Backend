"""Attach provider forecasts to dated itinerary days without relying on AI weather."""

import asyncio
from datetime import datetime, timedelta

from fastapi import HTTPException

from app.core.weather_cache import WeatherError
from app.core.weather_config import SRI_LANKA_TIME
from app.routers.weather import get_weather_service, normalize_location_query
from app.schemas.mvp_planner import ItineraryWeather


async def _forecast(service, destination):
    try:
        async with asyncio.timeout(5):
            locations = await service.locations(destination)
            if locations.locations:
                place = locations.locations[0]
                return await service.conditions(
                    "forecast", place.latitude, place.longitude
                )
    except (TimeoutError, WeatherError):
        return None


async def itinerary_weather(plan):
    if plan.start_date is None:
        return []
    today = datetime.now(SRI_LANKA_TIME).date()
    candidates = []
    for day in plan.days:
        trip_date = plan.start_date + timedelta(days=day.day - 1)
        if not 0 <= (trip_date - today).days <= 5:
            continue
        try:
            destination = normalize_location_query(day.destination)
        except HTTPException:
            continue
        candidates.append((day.day, destination, trip_date))
    if not candidates:
        return []
    try:
        service = get_weather_service()
    except HTTPException:
        return []
    destinations = list(dict.fromkeys(destination for _, destination, _ in candidates))[
        :3
    ]
    forecasts = dict(
        zip(
            destinations,
            await asyncio.gather(
                *(_forecast(service, destination) for destination in destinations),
            ),
        )
    )
    result = []
    for day, destination, trip_date in candidates:
        forecast = forecasts.get(destination)
        if forecast is None:
            continue
        daily = next(
            (item for item in forecast.daily_forecast if item.date == trip_date), None
        )
        if daily is not None:
            result.append(
                ItineraryWeather(
                    day=day,
                    destination=destination,
                    source=forecast.source,
                    fetched_at=forecast.fetched_at,
                    stale=forecast.stale,
                    forecast=daily,
                )
            )
    return result
