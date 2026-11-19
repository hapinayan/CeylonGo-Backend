# CeylonGo Live Weather Backend

Module 08 serves public, read-only weather data through the CeylonGo FastAPI API. The
approved module specification, `CeylonGo_Live_Weather_Module.pdf`, defines the feature scope.

## Architecture and configuration

The weather router validates inputs and translates service failures into HTTP responses.
The weather service calls the standard OpenWeather Current Weather, 5 Day / 3 Hour Forecast,
and Direct Geocoding APIs using HTTPX. It normalizes provider responses into Pydantic
schemas, groups forecasts by Sri Lankan calendar date, and attaches rule-based advice.
The application uses process-local memory and the shared sliding-window rate limiter.
It requires no database connection, schema changes, or migration.

Set these values in the backend's local `.env` and restart the backend after changing them.
Keep the actual key in backend configuration.

```dotenv
OPENWEATHER_API_KEY=your_api_key_here
OPENWEATHER_BASE_URL=https://api.openweathermap.org
WEATHER_CACHE_TTL_SECONDS=600
```

The base URL is an HTTPS origin. The TTL accepts whole seconds from 1 to 3,600.
OpenWeather currently lists the three standard APIs on its free plan at 60 calls/minute
and 1,000,000 calls/month. Confirm account entitlements and applicable license terms
before deployment. [Current Weather](https://openweathermap.org/api/current) ·
[5 Day / 3 Hour Forecast](https://openweathermap.org/api/forecast5) ·
[Direct Geocoding](https://openweathermap.org/api/geocoding-api) ·
[OpenWeather pricing](https://openweathermap.org/price)

## API endpoints

All routes are GET under `/api/v1/weather`. Use decimal degrees for `lat` and `lon`.
Latitude must be within −90 to 90 and longitude within −180 to 180. Weather coordinates
in the response are the normalized coordinates used upstream.

### Current weather

`GET /api/v1/weather/current?lat=6.9271&lon=79.8612`

```json
{
  "source": "OpenWeather",
  "source_url": "https://openweathermap.org/",
  "fetched_at": "2026-11-19T03:35:00Z",
  "stale": false,
  "coordinates": {"latitude": 6.927, "longitude": 79.861},
  "temperature_c": 28.4,
  "feels_like_c": 32.1,
  "condition": {"code": 500, "name": "Rain", "description": "light rain", "icon": "10d"},
  "humidity_percent": 79,
  "wind_speed_kmh": 13.32,
  "wind_direction_degrees": 225,
  "sunrise": "2026-11-19T06:05:00+05:30",
  "sunset": "2026-11-19T17:51:00+05:30",
  "observed_at": "2026-11-19T09:02:00+05:30"
}
```

Wind direction is in degrees clockwise from north and can be null.
Current observations, sunrise, and sunset are in Sri Lankan local time. The backend
fetch timestamp is UTC and describes when the cached payload was obtained.

### Forecast

`GET /api/v1/weather/forecast?lat=6.9271&lon=79.8612`

This example shows one interval on a partial day. Production responses include all
available intervals, up to 40, and can span six local date labels.

```json
{
  "source": "OpenWeather",
  "source_url": "https://openweathermap.org/",
  "fetched_at": "2026-11-19T03:35:00Z",
  "stale": false,
  "coordinates": {"latitude": 6.927, "longitude": 79.861},
  "timezone": "Asia/Colombo",
  "intervals": [{
    "forecast_at": "2026-11-19T23:30:00+05:30",
    "temperature_c": 28,
    "min_temperature_c": 26,
    "max_temperature_c": 29,
    "rain_probability_percent": 70,
    "wind_speed_kmh": 15.12,
    "condition": {"code": 500, "name": "Rain", "description": "light rain", "icon": "10n"}
  }],
  "days": [{
    "date": "2026-11-19",
    "interval_count": 1,
    "partial": true,
    "min_temperature_c": 26,
    "max_temperature_c": 29,
    "rain_probability_percent": 70,
    "condition": {"code": 500, "name": "Rain", "description": "light rain", "icon": "10n"},
    "recommendations": [
      "Pack rain gear and check trail conditions with your guide.",
      "Keep a museum, gallery or indoor workshop as an alternative.",
      "For eco-tourism, check park and trail access with the operator before departing."
    ],
    "cautions": ["Rain is possible. Trails and steps may be slippery."]
  }],
  "advisory_notice": "Travel guidance is advisory. Check official warnings and local conditions."
}
```

Days with fewer than eight intervals are partial. Temperature extrema summarize provider
values for the intervals present. The daily rain field is the highest available interval
probability, expressed as a percentage. A null means the provider supplied no probability
for that interval or day. The representative condition prioritizes thunderstorms, then
rain or drizzle, and chooses the interval nearest local noon within the selected group.
Advice uses forecast thunder, rain or drizzle, rain probability ≥60%, wind ≥40 km/h,
and heat ≥35 °C. Display cautions with the advisory notice.

### Sri Lankan destination search

`GET /api/v1/weather/locations?q=Kandy`

```json
{
  "source": "OpenWeather",
  "source_url": "https://openweathermap.org/",
  "fetched_at": "2026-11-19T03:35:00Z",
  "stale": false,
  "locations": [
    {"name": "Kandy", "region": "Central Province", "country": "LK",
     "latitude": 7.2906, "longitude": 80.6337}
  ]
}
```

Use a URL-encoded place name, 2–80 characters after whitespace and Unicode normalization.
The backend scopes the upstream query to Sri Lanka and returns only `LK` matches.
Region can be null. A successful search with no matches returns an empty list.

## Errors and cache behavior

Service errors use `{"detail":{"code":"weather_not_configured"}}`. Coordinate and query
parameter validation can also return the standard FastAPI validation array in `detail`.

| HTTP | Error code or cause | Client behavior |
| --- | --- | --- |
| 422 | Invalid coordinates or query, including `invalid_location_query` | Correct the input |
| 429 | `weather_quota_exceeded` | Wait for `Retry-After` |
| 502 | `weather_upstream_authentication`, `weather_invalid_response`, `weather_upstream_error` | Show weather unavailable |
| 503 | `weather_not_configured`, `weather_upstream_rate_limited`, `weather_upstream_unavailable`, `weather_busy` | Show weather unavailable and honor `Retry-After` when supplied |
| 504 | `weather_upstream_timeout` | Show weather unavailable and allow retry |

- Cache keys use the endpoint and coordinates rounded to three decimal places or the
  case-folded normalized destination query. The cache holds at most 512 entries with
  least-recently-used eviction, plus up to 32 distinct refreshes in flight. Concurrent
  requests for the same key share one refresh.
- Freshness defaults to 10 minutes. After a transient failure, the cache can serve a
  successful value for up to 30 minutes beyond its freshness window, with `stale: true`
  and the original `fetched_at`. A stale response can be reused during a short retry
  cooldown. Entries may be evicted sooner. Authentication errors surface directly.
- Requests have a two-second connect timeout, three-second HTTP phase timeouts, and
  a four-second deadline per attempt. Network failures, timeouts and HTTP 5xx get at
  most one retry. Upstream authentication failures and 429s return immediately.
  An upstream 429 applies a process-local cooldown based on numeric `Retry-After`
  where available, otherwise 60 seconds.
- Each process allows up to 20 upstream attempts in a rolling 60-second window,
  including retries. Each worker has its own cache and limit. Budget deployment
  workers and any other consumers against the account quota.
- The `app.services.weather` logger records endpoint names, upstream statuses, retry
  outcomes and cache outcomes. Enable INFO for request-usage monitoring in the
  deployment log sink. Weather logs omit the key, user query and upstream payload.

## Angular and Flutter integration

- Angular 21 can use a reusable `HttpClient` weather service and query parameters.
  Flutter 3 can use a weather repository with typed JSON models and the same routes.
  Use the configured CeylonGo base URL and load current weather and forecast
  independently so one can render if the other fails.
- After location permission is granted, send GPS coordinates as `lat` and `lon`.
  Offer debounced destination search and manual selection when permission is declined
  or GPS is unavailable. Pass the selected location's coordinates to both weather routes.
- Homestay, Activities, Eco-Tourism, Tour Guide and Interactive Map can pass their
  existing coordinates. The AI Smart Travel Planner can consume interval and day
  fields to propose alternatives, including the caution and advisory text as context.
- Show source attribution and `fetched_at`, plus a distinct stale label when needed.
  Preserve the Sri Lankan date and time in the display. JavaScript and Dart can
  convert parsed timestamps into the device zone, so format with an explicit
  `Asia/Colombo` zone or render the provided local wall time. Use `days[].date` for
  headings and `partial` for coverage labels.
- Weather cards, location permission, loading/offline states, advisory UI and manual
  refresh are client work. Manual refresh repeats the same GETs and respects the backend
  TTL. Automatic refresh should run only while the screen is active and honor
  `Retry-After`. Client builds and cross-platform UI checks remain pending.

## Requirements traceability

| ID | Backend delivery | Client work |
| --- | --- | --- |
| W01 | Coordinate-based current weather | Request GPS permission and send coordinates |
| W02 | Validated Sri Lankan geocoding | Destination search and selection |
| W03 | `temperature_c` | Display Celsius |
| W04 | `feels_like_c` | Display apparent temperature |
| W05 | Condition text, code and icon | Render weather label and icon |
| W06 | `humidity_percent` | Display relative humidity |
| W07 | km/h and direction in degrees | Display wind and compass direction |
| W08 | Forecast interval percentage and daily maximum | Display probability and unavailable values |
| W09 | Sunrise and sunset at UTC+05:30 | Display Sri Lankan local time |
| W10 | Local daily summaries with partial coverage | Render day cards and coverage labels |
| W11 | Chronological three-hour forecast intervals | Render interval view |
| W12 | Rule-based travel recommendations | Display recommendations in relevant views |
| W13 | Daily in-app cautions and advisory notice | Render advisory cautions |
| W14 | Repeated GETs use the freshness policy | Add manual refresh control |
| W15 | GETs support periodic polling | Refresh only while the screen is active |

## Verification

- Isolated mocked tests on 19 November 2026. **16 passed** with
  `python -m pytest -p no:cacheprovider -q tests/test_weather.py`, under a 60-second
  subprocess deadline. The suite uses an in-process FastAPI router and HTTPX mock
  transport. The main application OpenAPI schema separately confirmed all three routes.
- Pre-commit verification on 19 November 2026. **19 focused tests passed** under a
  60-second subprocess deadline. This reran the 16 weather tests and three existing
  rate-limiter regressions for check-email, forgot-password, and provider OTP send.
  The shared regressions used isolated in-memory SQLite and emitted one Starlette
  TestClient deprecation warning.
- Live smoke tests on 19 November 2026 used the local backend key with a 40-second
  overall deadline. The table records successful OpenWeather responses normalized
  by the weather service. Geocoding counts are Sri Lankan results.

| City | Current | Forecast | Direct geocoding |
| --- | --- | --- | --- |
| Colombo | 200 | 200, 40 intervals | 200, 1 match |
| Kandy | 200 | 200, 40 intervals | 200, 2 matches |
| Jaffna | 200 | 200, 40 intervals | 200, 1 match |
| Ella | 200 | 200, 40 intervals | 200, 2 matches |

- Database verification was limited to the repository change set. No database
  connection, migration execution, or database write was needed.
