from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class WeatherModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class Coordinates(WeatherModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Condition(WeatherModel):
    code: int
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=160)
    icon: str = Field(pattern=r"^[0-9]{2}[dn]$")


class WeatherMetadata(WeatherModel):
    source: str = "OpenWeather"
    source_url: str = "https://openweathermap.org/"
    fetched_at: datetime
    stale: bool = False


class CurrentWeather(WeatherMetadata):
    coordinates: Coordinates
    temperature_c: float
    feels_like_c: float
    condition: Condition
    humidity_percent: int = Field(ge=0, le=100)
    wind_speed_kmh: float = Field(ge=0)
    wind_direction_degrees: int | None = Field(ge=0, le=360)
    sunrise: datetime
    sunset: datetime
    observed_at: datetime


class ForecastInterval(WeatherModel):
    forecast_at: datetime
    temperature_c: float
    min_temperature_c: float
    max_temperature_c: float
    rain_probability_percent: float | None = Field(ge=0, le=100)
    wind_speed_kmh: float = Field(ge=0)
    condition: Condition


class DailyForecast(WeatherModel):
    date: date
    interval_count: int
    partial: bool
    min_temperature_c: float
    max_temperature_c: float
    rain_probability_percent: float | None
    condition: Condition
    recommendations: list[str]
    cautions: list[str]


class Forecast(WeatherMetadata):
    coordinates: Coordinates
    timezone: str = "Asia/Colombo"
    intervals: list[ForecastInterval]
    days: list[DailyForecast]
    advisory_notice: str = (
        "Travel guidance is advisory. Check official warnings and local conditions."
    )


class Location(Coordinates):
    name: str = Field(min_length=1, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    country: str = "LK"


class Locations(WeatherMetadata):
    locations: list[Location]
