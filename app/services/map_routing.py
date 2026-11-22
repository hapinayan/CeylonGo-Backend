import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable

import httpx
from fastapi import HTTPException

from app.core.map_config import MapSettings
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.schemas.interactive_map import LineGeometry, MapRoute, RouteRequest

logger = logging.getLogger(__name__)
MODES = {"driving": "drive", "walking": "walk", "cycling": "bicycle"}
ATTRIBUTION = [
    {"label": "Powered by Geoapify", "url": "https://www.geoapify.com/"},
    {
        "label": "© OpenStreetMap contributors",
        "url": "https://www.openstreetmap.org/copyright",
    },
]


class RouteError(Exception):
    def __init__(self, status: int, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_after = retry_after


class RoutingTransport(httpx.AsyncBaseTransport):
    """Keep waypoint and credential parameters out of HTTPX request logging."""

    def __init__(self, transport: httpx.AsyncBaseTransport, params: dict[str, str]):
        self.transport = transport
        self.params = params

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        upstream = httpx.Request(
            request.method,
            request.url,
            params=self.params,
            headers=request.headers,
            extensions=request.extensions,
        )
        return await self.transport.handle_async_request(upstream)

    async def aclose(self) -> None:
        await self.transport.aclose()


def normalize_route(payload: dict, request: RouteRequest) -> MapRoute:
    try:
        features = payload["features"]
        if payload["type"] != "FeatureCollection" or not isinstance(features, list):
            raise ValueError
        if not features:
            raise RouteError(404, "map_no_route")
        feature = features[0]
        raw_geometry = feature["geometry"]
        lines = raw_geometry["coordinates"]
        if raw_geometry["type"] == "LineString":
            lines = [lines]
        elif raw_geometry["type"] != "MultiLineString":
            raise ValueError
        geometry = LineGeometry(coordinates=lines)
        props = feature["properties"]
        distance = props["distance"]
        duration = props["time"]
        instructions = [
            {
                "text": step["instruction"]["text"],
                "distance_m": step.get("distance"),
                "duration_seconds": step.get("time"),
            }
            for leg in props.get("legs", [])
            for step in leg.get("steps", [])
            if isinstance(step.get("instruction"), dict)
            and step["instruction"].get("text")
        ]
        return MapRoute(
            mode=request.mode,
            geometry=geometry,
            distance_m=distance,
            distance_km=distance / 1000,
            duration_seconds=duration,
            duration_minutes=duration / 60,
            instructions=instructions,
            attribution=ATTRIBUTION,
        )
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        raise RouteError(502, "map_invalid_route_response") from None


class MapRoutingService:
    def __init__(
        self,
        settings: MapSettings,
        transport_factory: Callable[
            [], httpx.AsyncBaseTransport
        ] = httpx.AsyncHTTPTransport,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.transport_factory = transport_factory
        self.clock = clock
        self.cache: OrderedDict[tuple, tuple[float, MapRoute]] = OrderedDict()
        self.pending: dict[tuple, asyncio.Task[MapRoute]] = {}
        self.quotas = (
            SlidingWindowRateLimiter(4, 1),
            SlidingWindowRateLimiter(1000, 86400),
        )
        self.cooldown_until = 0.0

    async def route(self, request: RouteRequest) -> MapRoute:
        if not self.settings.api_key:
            raise RouteError(503, "map_routing_not_configured")
        points = [
            (round(point.latitude, 6) or 0, round(point.longitude, 6) or 0)
            for point in (request.origin, request.destination)
        ]
        if points[0] == points[1]:
            raise RouteError(422, "map_identical_waypoints")
        key = (request.mode, *points)
        cached = self.cache.pop(key, None)
        if cached and self.clock() < cached[0]:
            self.cache[key] = cached
            logger.info("map routing result=cache_hit")
            return cached[1].model_copy(deep=True)
        task = self.pending.get(key)
        if task is None:
            if len(self.pending) >= 16:
                raise RouteError(503, "map_routing_busy", 1)
            waypoints = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in points)
            task = asyncio.create_task(self._load(key, request, waypoints))
            self.pending[key] = task
            task.add_done_callback(self._observe)
        return (await asyncio.shield(task)).model_copy(deep=True)

    @staticmethod
    def _observe(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _load(
        self, key: tuple, request: RouteRequest, waypoints: str
    ) -> MapRoute:
        try:
            result = await self._request(request, waypoints)
            self.cache[key] = (self.clock() + self.settings.cache_ttl_seconds, result)
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            return result
        except RouteError as error:
            logger.warning("map routing result=%s", error.code)
            raise
        finally:
            self.pending.pop(key, None)

    def _check_quota(self) -> None:
        remaining = self.cooldown_until - self.clock()
        if remaining > 0:
            raise RouteError(503, "map_upstream_rate_limited", int(remaining) + 1)
        try:
            for quota in self.quotas:
                quota.check_identifier("geoapify")
        except HTTPException as error:
            raise RouteError(
                429,
                "map_quota_exceeded",
                int(error.headers["Retry-After"]),
            ) from None

    async def _request(self, request: RouteRequest, waypoints: str) -> MapRoute:
        transport = RoutingTransport(
            self.transport_factory(),
            {
                "apiKey": self.settings.api_key,
                "waypoints": waypoints,
                "mode": MODES[request.mode],
                "format": "geojson",
                "units": "metric",
            },
        )
        async with httpx.AsyncClient(
            base_url=self.settings.base_url,
            transport=transport,
            timeout=httpx.Timeout(4, connect=2),
            follow_redirects=False,
        ) as client:
            for attempt in range(2):
                self._check_quota()
                try:
                    async with asyncio.timeout(5):
                        return normalize_route(await self._fetch(client), request)
                except (TimeoutError, httpx.TimeoutException):
                    error = RouteError(504, "map_upstream_timeout")
                except (httpx.RequestError, httpx.StreamError):
                    error = RouteError(503, "map_upstream_unavailable")
                except RouteError as upstream:
                    if upstream.code != "map_upstream_unavailable":
                        raise
                    error = upstream
                if attempt == 0:
                    await asyncio.sleep(0.2)
            raise error

    async def _fetch(self, client: httpx.AsyncClient) -> dict:
        async with client.stream("GET", "/v1/routing") as response:
            status = response.status_code
            logger.info("map routing upstream_status=%d", status)
            if status in (401, 403):
                raise RouteError(502, "map_upstream_authentication")
            if status == 429:
                header = response.headers.get("Retry-After", "")
                delay = (
                    min(3600, max(1, int(header)))
                    if header.isascii() and header.isdecimal() and len(header) <= 10
                    else 60
                )
                self.cooldown_until = max(self.cooldown_until, self.clock() + delay)
                raise RouteError(503, "map_upstream_rate_limited", delay)
            if status >= 500:
                raise RouteError(503, "map_upstream_unavailable")
            if status not in (200, 400):
                raise RouteError(502, "map_upstream_error")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                body.extend(chunk)
                if len(body) > 512 * 1024:
                    raise RouteError(502, "map_invalid_route_response")
            try:
                payload = json.loads(body)
            except (ValueError, RecursionError):
                raise RouteError(502, "map_invalid_route_response") from None
            if not isinstance(payload, dict):
                raise RouteError(502, "map_invalid_route_response")
            if status == 400:
                if (
                    str(payload.get("message", ""))
                    .lower()
                    .startswith(
                        (
                            "no path could be found",
                            "no suitable edges",
                            "too long distance between locations",
                        )
                    )
                ):
                    raise RouteError(404, "map_no_route")
                raise RouteError(502, "map_upstream_error")
            return payload
