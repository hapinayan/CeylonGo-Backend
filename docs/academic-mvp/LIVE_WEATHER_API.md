# Live Weather API

The Academic MVP uses the existing OpenWeather Current Weather, 5 Day / 3 Hour Forecast, and Direct Geocoding integrations. All routes below accept guests.

| Endpoint | Query |
|---|---|
| GET `/api/v1/weather/current` | `lat` and `lon`, or `location` |
| GET `/api/v1/weather/forecast` | `lat` and `lon`, or `location` |
| GET `/api/v1/weather/search` | `q` |
| GET `/api/v1/weather/locations` | Existing alias for destination search |

Supply decimal latitude from -90 to 90 and longitude from -180 to 180. Supply both coordinates together. For a name such as `location=Colombo`, the server resolves the first Sri Lankan geocoding match. For ambiguous names, search first and send the coordinates of the match the traveler selects. Search normalizes whitespace and Unicode and accepts names of 2–80 characters after normalization. No-match search returns an empty list. A direct weather lookup by an unmatched location returns HTTP 404 with `weather_location_not_found`.

## Examples

`GET /api/v1/weather/search?q=Colombo` returns this shape:

```json
{
  "source": "OpenWeather",
  "source_url": "https://openweathermap.org/",
  "fetched_at": "2026-11-27T08:00:00Z",
  "stale": false,
  "locations": [
    {"name": "Colombo", "region": "Western Province", "country": "LK",
     "latitude": 6.9271, "longitude": 79.8612}
  ]
}
```

`GET /api/v1/weather/current?lat=6.9271&lon=79.8612` returns this shape. Example values are illustrative.

```json
{
  "source": "OpenWeather",
  "source_url": "https://openweathermap.org/",
  "fetched_at": "2026-11-27T08:00:00Z",
  "stale": false,
  "coordinates": {"latitude": 6.927, "longitude": 79.861},
  "temperature_c": 28,
  "feels_like_c": 31,
  "condition": {"code": 803, "name": "Clouds", "description": "broken clouds", "icon": "04d"},
  "humidity_percent": 77,
  "wind_speed_kmh": 13.5,
  "wind_direction_degrees": 240,
  "sunrise": "2026-11-27T06:06:00+05:30",
  "sunset": "2026-11-27T17:53:00+05:30",
  "observed_at": "2026-11-27T13:25:00+05:30"
}
```

`GET /api/v1/weather/forecast?location=Colombo` adds `timezone`, `intervals`, `days`, `daily_forecast`, and `advisory_notice` to the source and coordinate metadata. Use `daily_forecast` for up to five local calendar dates. The existing `days` field retains every local date spanned by the 40 three-hour intervals, which can include a sixth partial date. Each interval has a `forecast_at` timestamp. This is an example daily item:

```json
{
  "date": "2026-11-27",
  "interval_count": 4,
  "partial": true,
  "min_temperature_c": 25.8,
  "max_temperature_c": 29.2,
  "rain_probability_percent": 80,
  "condition": {"code": 500, "name": "Rain", "description": "light rain", "icon": "10d"},
  "recommendations": ["Pack rain gear and check trail conditions with your guide."],
  "cautions": ["Rain is possible. Trails and steps may be slippery."]
}
```

Daily temperatures are the low and high among available intervals. Daily rain probability is the highest supplied interval probability and is `null` when unavailable. Forecast intervals, observations, and daily grouping use `Asia/Colombo`. Cautions provide travel guidance. Consult official Sri Lankan warnings for emergency decisions.

## Configuration and failures

Set `OPENWEATHER_API_KEY` in the backend environment. Keep it server-side. The optional `OPENWEATHER_BASE_URL` uses an HTTPS origin and defaults to the OpenWeather API origin. `WEATHER_CACHE_TTL_SECONDS` defaults to 600 and accepts 1–3600. Cache and upstream throttling state are per process. A transient provider failure can return previously fetched data with `stale: true` during the existing bounded stale window. Display the observation or forecast timestamp and stale status.

| HTTP status | Meaning |
|---|---|
| 404 | Direct lookup by location found no Sri Lankan match |
| 422 | Invalid or incomplete input |
| 429 | The backend weather quota is exhausted |
| 502 | Upstream authentication, rejected request, or invalid provider response |
| 503 | Weather unavailable, unconfigured, busy, or throttled upstream |
| 504 | Upstream timeout |

Provider errors include a machine-readable `detail.code`. Honor `Retry-After` when present. Failures without eligible cached data return an error.

## Angular and Flutter

Debounce destination search, let users select a result, and request current weather and forecast with its coordinates. Render the provider attribution, Celsius, local date and time, stale status, and forecast cautions. Use `condition.icon` to choose an icon and omit rain probability when it is null. Display an unavailable state for errors.

Dated AI planner conversations can include `itinerary.weather` entries keyed by itinerary day. Those entries carry a matching daily forecast, provider name, fetch time, and stale flag. Weather is best-effort for up to three destinations per response. Undated trips, unsupported dates, unmatched destinations, and provider failures without a usable response leave those days without a weather entry. Saved itineraries retain the original fetch time.
