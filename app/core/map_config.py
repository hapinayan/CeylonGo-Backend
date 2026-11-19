import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class MapSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.geoapify.com"
    cache_ttl_seconds: int = 600

    @classmethod
    def from_env(cls) -> "MapSettings":
        try:
            ttl = int(os.getenv("MAP_CACHE_TTL_SECONDS", "600"))
            base_url = os.getenv("GEOAPIFY_BASE_URL", cls.base_url).strip().rstrip("/")
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
            raise ValueError("Invalid routing configuration") from None
        key = os.getenv("GEOAPIFY_API_KEY", "").strip()
        return cls("" if key == "your_api_key_here" else key, base_url, ttl)
