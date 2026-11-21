import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from app.schemas.weather import WeatherMetadata


T = TypeVar("T", bound=WeatherMetadata)


class WeatherError(Exception):
    def __init__(
        self, status: int, code: str, *, transient: bool = False,
        retry_after: int | None = None,
    ):
        super().__init__(code)
        self.status = status
        self.code = code
        self.transient = transient
        self.retry_after = retry_after


@dataclass
class CacheEntry:
    value: WeatherMetadata
    stored_at: float
    retry_at: float = 0


class WeatherCache(Generic[T]):
    def __init__(
        self, ttl: int, *, capacity: int = 512, stale_seconds: int = 1800,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl = ttl
        self.capacity = capacity
        self.stale_seconds = stale_seconds
        self.clock = clock
        self.entries: OrderedDict[tuple, CacheEntry] = OrderedDict()
        self.pending: dict[tuple, asyncio.Task[WeatherMetadata]] = {}

    async def get(self, key: tuple, load: Callable[[], Awaitable[T]]) -> tuple[T, str]:
        now = self.clock()
        entry = self.entries.get(key)
        if entry:
            self.entries.move_to_end(key)
            age = now - entry.stored_at
            if age < self.ttl:
                return cast(T, entry.value.model_copy(deep=True)), "hit"
            if age >= self.ttl + self.stale_seconds:
                del self.entries[key]
                entry = None
            elif now < entry.retry_at:
                return cast(T, self._stale(entry)), "stale"

        task = self.pending.get(key)
        outcome = "coalesced"
        if task is None:
            if len(self.pending) >= 32:
                if entry is not None:
                    return cast(T, self._stale(entry)), "stale"
                raise WeatherError(503, "weather_busy", retry_after=1)
            task = asyncio.create_task(self._load(key, load, entry))
            self.pending[key] = task
            task.add_done_callback(self._observe_result)
            outcome = "miss"
        value = await asyncio.shield(task)
        return cast(T, value.model_copy(deep=True)), "stale" if value.stale else outcome

    @staticmethod
    def _observe_result(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _stale(entry: CacheEntry) -> WeatherMetadata:
        return entry.value.model_copy(deep=True, update={"stale": True})

    async def _load(
        self, key: tuple, load: Callable[[], Awaitable[T]], entry: CacheEntry | None,
    ) -> WeatherMetadata:
        try:
            value = await load()
            self.entries[key] = CacheEntry(value, self.clock())
            self.entries.move_to_end(key)
            if len(self.entries) > self.capacity:
                self.entries.popitem(last=False)
            return value
        except WeatherError as error:
            now = self.clock()
            if (
                entry is not None
                and error.transient
                and now - entry.stored_at < self.ttl + self.stale_seconds
            ):
                entry.retry_at = now + max(15, error.retry_after or 0)
                return self._stale(entry)
            raise
        finally:
            self.pending.pop(key, None)
