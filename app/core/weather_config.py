import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from urllib.parse import urlsplit


SRI_LANKA_TIME = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")


@dataclass(frozen=True)
class WeatherSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.openweathermap.org"
    cache_ttl_seconds: int = 600

    @classmethod
    def from_env(cls) -> "WeatherSettings":
        base_url = os.getenv("OPENWEATHER_BASE_URL", cls.base_url).strip().rstrip("/")
        try:
            ttl = int(os.getenv("WEATHER_CACHE_TTL_SECONDS", "600"))
            url = urlsplit(base_url)
            if (
                not 1 <= ttl <= 3600
                or url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.path
                or url.query
                or url.fragment
            ):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ValueError("Invalid weather configuration") from None
        key = os.getenv("OPENWEATHER_API_KEY", "").strip()
        if key == "your_api_key_here":
            key = ""
        return cls(key, base_url, ttl)
