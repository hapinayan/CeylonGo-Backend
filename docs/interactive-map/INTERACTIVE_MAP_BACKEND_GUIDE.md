# CeylonGo Interactive Map Backend

Module 09 exposes one listing catalog and routing contract for Angular and Flutter.
FastAPI validates and authorizes requests, SQLAlchemy projects existing SQL Server
listings into a public read model, and HTTPX calls Geoapify Routing. Geoapify replaces
OpenRouteService for this module. Clients load OpenStreetMap tiles directly.
The approved feature scope is defined in `CeylonGo_Interactive_Map_Module.pdf`.

## Configuration

```dotenv
GEOAPIFY_API_KEY=your_api_key_here
GEOAPIFY_BASE_URL=https://api.geoapify.com
MAP_CACHE_TTL_SECONDS=600
```

Set the real key only in the backend environment and restart after configuration
changes. The base URL is an HTTPS origin, and the TTL accepts 1–3,600 whole seconds.
The application calls `GET /v1/routing` with two waypoints in `latitude,longitude`
order. The modes map to `drive`, `walk`, and `bicycle`. Output GeoJSON always uses
`[longitude, latitude]` positions. [Geoapify Routing API](https://apidocs.geoapify.com/docs/routing/routing)

## Endpoint inventory

| Method and path | Inputs and output |
| --- | --- |
| `GET /api/v1/map/locations` | Required `west,south,east,north`. Returns approved marker page. |
| `GET /api/v1/map/nearby` | `lat,lon,radius_km`. Returns a page ranked by straight-line distance. Radius defaults to 10 and accepts 5, 10, 25, or 50 km. |
| `GET /api/v1/map/search` | `q`. Literal case-insensitive substring search across listing name, city, and district. |
| `GET /api/v1/map/categories` | Category identifiers, display labels and availability with a reason where unavailable. |
| `POST /api/v1/map/route` | Origin, destination, and `driving`, `walking`, or `cycling`. Defaults to `driving`. |
| `GET /api/v1/admin/map/locations` | Admin-only review queue. Optional `module`, plus pagination. Includes draft, inactive, and missing-coordinate records. |
| `PATCH /api/v1/admin/map/locations/{module}/{listing_id}` | Admin-only coordinate correction with a required reason. |
| `POST /api/v1/admin/map/locations/{module}/{listing_id}/decision` | Admin-only approval or rejection for `tour_guide` and `rental`. |
| `POST /api/v1/provider/tour-guides/submit` | Submit the owned draft or rejected guide with a public base point for review. |
| `GET /api/v1/provider/rental-locations` | List the approved rental provider’s own pickup records, 50 per page. |
| `POST /api/v1/provider/rental-locations` | Create an owned inactive pickup record for admin review. |
| `PUT /api/v1/provider/rental-locations/{location_id}` | Replace an owned pickup location and return it to inactive review if changed. |

Public catalog requests accept optional `category`, `page` (1–10,000) and `page_size`
(1–100, default 25). A known unavailable category returns an empty page.
The location identity is `(module_type, listing_id)`. The `detail_path` field is
nullable when the source has no public details API. The admin rental identifier is
`rental_locations.id`. Public rental markers identify the linked vehicle.
Provider and admin endpoints require their respective bearer-authenticated roles.

### Catalog examples

```http
GET /api/v1/map/locations?west=79.7&south=6.7&east=80.3&north=7.2&category=homestay
GET /api/v1/map/nearby?lat=6.93&lon=79.85&radius_km=5&category=homestay
GET /api/v1/map/search?q=Colombo&category=homestay&page=1
GET /api/v1/map/categories
```

```json
{
  "items": [{
    "listing_id": 12, "module_type": "homestay", "name": "Colombo Stay",
    "category": "homestay", "latitude": 6.94, "longitude": 79.86,
    "address": "Public property entrance", "city": "Colombo", "district": "Colombo",
    "image_url": null, "rating": 4.5, "price": 8500, "currency": "LKR",
    "detail_path": "/api/v1/homestays/12", "distance_km": 1.57
  }],
  "page": 1, "page_size": 25, "has_more": false, "distance_type": "straight_line"
}
```

The example page shows the nearby response. Locations and search return null for
both distance fields. Example category entries are
`{"id":"homestay","label":"Homestays","available":true,"reason":null}` and
`{"id":"tourist_attraction","label":"Tourist attractions","available":false,"reason":"There is no standalone tourist-attraction catalog."}`.

### Routing example

```json
{
  "origin": {"latitude": 6.9345, "longitude": 79.8501},
  "destination": {"latitude": 6.9272, "longitude": 79.8439},
  "mode": "walking"
}
```

An illustrative response from `POST /api/v1/map/route` follows with simplified
geometry and instructions.

```json
{
  "mode": "walking",
  "geometry": {"type": "MultiLineString", "coordinates": [
    [[79.8501, 6.9345], [79.8439, 6.9272]]
  ]},
  "distance_m": 1583, "distance_km": 1.583,
  "duration_seconds": 1551.273, "duration_minutes": 25.85455,
  "instructions": [{"text": "Continue on the footpath", "distance_m": 80, "duration_seconds": 75}],
  "attribution": [
    {"label": "Powered by Geoapify", "url": "https://www.geoapify.com/"},
    {"label": "© OpenStreetMap contributors", "url": "https://www.openstreetmap.org/copyright"}
  ]
}
```

### Management examples

- An admin lists records with `GET /api/v1/admin/map/locations?module=activity`.
  A review item contains a source identifier, name, address, city, district,
  coordinates, approval status and active flag.
- For `PATCH /api/v1/admin/map/locations/activity/12`, send
  `{"latitude":6.94,"longitude":79.86,"reason":"Verified the public entrance"}`.
  The response is the review item with corrected coordinates. The prior listing
  approval state is retained.
- For `POST /api/v1/admin/map/locations/tour_guide/15/decision`, send
  `{"decision":"approved","reason":"Verified the public base location"}`.
  The response is the approved review item. This workflow does not verify credentials.
- To create or replace a rental pickup location, send
  `{"location_name":"Fort depot","district":"Colombo","city":"Fort","address":"Public shop entrance","latitude":6.94,"longitude":79.86}`.
  The response adds `id` and `is_active`. After approval, send
  `{"location_id":12}` to the existing rental vehicle create or update endpoint.

## Publication and database mapping

Every public row needs an approved provider profile backed by an active user,
a valid coordinate pair, and the source-specific publication criteria below.
The catalog selects only allowlisted public fields. It never falls back to
provider onboarding addresses, identity data, or a guessed location.

| Category | Source and public fields | Publication rule |
| --- | --- | --- |
| `homestay` | `homestays` name, address, district, city, latitude/longitude, nightly LKR price, primary image | Approved and active, linked provider profile |
| `adventure`, `cultural`, `local_activity`, `community_activity` | `activities` title, meeting point, city, district, latitude/longitude, price/currency, cover image | Approved and active, category mapped from the existing activity type |
| `eco_tourism` | `eco_experiences` title, public address, city/district, latitude/longitude, base price/currency, primary image | Approved and published |
| `tour_guide` | `tour_guides` public base city/district/pair, provider business name, daily rate, currency, profile image and aggregate rating | Approved and active. Guide license and private provider address are excluded. |
| `artisan` | `artisan_listings` title, public address, city/district, latitude/longitude and cover image | Approved, active, and verified through the existing admin status API |
| `bike_rental`, `bicycle_rental` | `rental_vehicles` name and base daily LKR price, joined to `rental_locations` for the pickup address, city/district and coordinates | Approved available vehicle with an active pickup location owned by the same provider |
| `local_business`, `tourist_attraction` | No independent published listing catalog | Unavailable. Artisan shops are available under `artisan`. |

Where present, published review averages come from `reviews` and
`listing_references`, keyed by module and target ID. Listings missing required
links or coordinates remain absent until their source records are corrected.

Bounding boxes are inclusive. A west greater than east denotes a box crossing
the antimeridian. Nearby uses a conservative bounding box before evaluating
SQL Server `geography::Point(..., 4326).STDistance(...)`, then filters, orders,
and paginates in SQL. Tests use SQL Haversine on SQLite. Distance ties are broken
by module and ID. Catalog search is parameterized and treats LIKE wildcard
characters as literals. Pagination uses `has_more` rather than an expensive total count.

## Provider and administrator workflow

- Use existing owned Homestay, Activity, Eco-Tourism, Tour Guide and Artisan
  listing endpoints. Coordinates are validated as pairs. Coordinate fields can
  be cleared where optional by clearing both together.
- Homestay and Eco-Tourism location edits to approved listings return the listing
  to draft. Location edits during review are rejected. Use the existing submit
  and admin decision routes. Administrators use
  `POST /api/v1/admin/homestays/{id}/review` or `POST` to
  `/api/v1/admin/eco-experiences/{id}/approve` or `/reject`.
  Activities keep their draft/rejected-only edit policy and use the existing
  `POST /api/v1/admin/activities/{id}/approve` or `/reject` routes.
- Artisan location changes clear the verification flag. An administrator verifies
  or disables the listing through `PATCH /api/v1/artisan-listings/{id}/status`.
  A provider cannot set verification through the listing edit route.
- Tour Guide providers save the public base location, then use the submit endpoint.
  An administrator approves or rejects the pending guide. A change to a previously
  approved base location makes it draft and inactive until reapproved.
- Rental pickup records use the existing `is_active` flag to control visibility.
  Saving a changed pickup turns it inactive for all linked vehicles until approved
  again. Rejection leaves the pickup inactive and records the decision in the audit
  table. The pickup row cannot distinguish rejected from awaiting review on its own.
- Admin corrections preserve approval state, enforce authorization, and atomically
  record actor, source entity ID, before/after coordinate values and reason in
  `admin_audit_logs`. An unchanged correction is idempotent. The existing
  Homestay admin edit path also audits coordinate changes. The SQLAlchemy audit
  mapping was aligned with the physical `action_type` and `entity_type` columns.

### Schema changes requiring approval

- For MAP-AD-05, propose `dbo.tourist_attractions` with `id INT IDENTITY` primary
  key, `name NVARCHAR(150) NOT NULL`, nullable `description NVARCHAR(2000)`,
  `public_address NVARCHAR(500)` and `image_url NVARCHAR(1000)`,
  `city NVARCHAR(100) NOT NULL`, `district NVARCHAR(100) NOT NULL`,
  nullable `latitude DECIMAL(9,6)` and `longitude DECIMAL(9,6)`,
  `approval_status NVARCHAR(20) NOT NULL DEFAULT 'draft'`,
  `is_active BIT NOT NULL DEFAULT 0`, nullable
  `reviewed_by INT REFERENCES dbo.users(id)`, nullable `reviewed_at DATETIME2`,
  and `created_at` and `updated_at` as non-null `DATETIME2` with UTC defaults.
  Check status against draft, pending, approved and rejected. Require both
  coordinates to be null or both within range, and require both before publication.
  Index `(approval_status, is_active, district)` and reuse `admin_audit_logs`.
- A separate local-business catalog would additionally require an approved listing
  contract with `provider_profile_id INT NOT NULL REFERENCES dbo.provider_profiles(id)`
  and its own review workflow. Provider registration cannot serve as that catalog.
- If guides require a public street address for MAP-SP-03, propose
  `dbo.tour_guides.public_base_address NVARCHAR(500) NULL` under the existing
  guide location review. A provider's onboarding address stays private.

## Routing errors, caching and quotas

| Status | Meaning |
| --- | --- |
| 404 | No route from Geoapify or missing owned/admin location |
| 409 | Location changed concurrently or decision is out of sequence |
| 422 | Invalid input, unsupported radius, identical waypoints, or missing coordinates for approval |
| 429 | Local routing budget exhausted |
| 502 | Provider authentication rejected, malformed response, or unexpected status |
| 503 | Missing routing config, provider outage or cooldown, or local routing saturation |
| 504 | Upstream deadline or timeout |

Service errors include a stable code such as `{"detail":{"code":"map_no_route"}}`.
Validation errors can use FastAPI's normal detail list. Use `Retry-After` when
present. An unavailable route has no estimated geometry, distance or time.

- Cache up to 128 successful routes per process for the configured TTL.
  The key is mode plus the ordered, six-decimal waypoint pairs. Concurrent
  requests for one key coalesce, with at most 16 distinct loads in flight.
- Each attempt has a two-second connect timeout, four-second HTTP phase timeout,
  and five-second total deadline. Timeouts, network errors and 5xx allow one
  retry. Authentication errors, no-route outcomes and 429 are returned immediately.
  A 429 triggers a process-local cooldown using a numeric Retry-After where available.
- Limit upstream attempts to four per second and 1,000 per rolling day per
  process, including retries. Geoapify lists 3,000 free credits/day and up to
  five requests/second. Two-waypoint routes ordinarily cost one credit, with
  additional credits for longer routes. Share or partition the budget before
  running multiple workers or Geoapify consumers. [Geoapify pricing](https://www.geoapify.com/pricing/) ·
  [Credit calculation](https://www.geoapify.com/pricing-details/)
- Routing logs contain the outcome and status. HTTPX logs omit API keys and
  waypoint query parameters. The app removes nearby query strings from Uvicorn
  access logs. Apply the same privacy rule to reverse proxies and telemetry sinks.

## Angular and Flutter

Use Angular 21 with Leaflet and Flutter 3 with `flutter_map`. Load tiles directly
from a configurable URL. For OpenStreetMap's public raster service use
`https://tile.openstreetmap.org/{z}/{x}/{y}.png`. Keep linked
**© OpenStreetMap contributors** visible. Web requests need a valid Referer.
Native apps must identify themselves with a stable User-Agent. Honor tile caching
headers, cache for at least seven days if headers cannot be read, and avoid bulk
downloads or offline prefetch. Display a linked **Powered by Geoapify** near
Geoapify route data. [OSMF tile policy](https://operations.osmfoundation.org/policies/tiles/) ·
[Geoapify attribution](https://www.geoapify.com/pricing/)

Fetch viewport markers after map movement settles, and drive filters from
`/categories`. Use location permission or a manually selected point for nearby
and route origins. Draw the GeoJSON lines with the library's GeoJSON adapter or
convert each position explicitly to its latitude/longitude type. Keep straight-line
and routed distances labeled distinctly. Use the listing coordinates with Live
Weather and pass listing IDs, category and route estimates to AI itinerary or
route planning modules. Advanced multi-stop optimization is outside this phase.
Clustering, marker layouts, list toggles, GPS prompts, external navigation and
client builds remain client work.

## Requirement traceability

| ID | Status | Scope |
| --- | --- | --- |
| MAP-01 | Client-side pending | Sri Lanka tile rendering, pan and zoom |
| MAP-02 | Client-side pending | GPS permission and position display |
| MAP-03 | Completed | Approved name, city and district search |
| MAP-04 | Completed | Category discovery and filtering across catalog endpoints |
| MAP-05 | Partial | Marker coordinates and categories supplied. Pins and interactions remain client work. |
| MAP-06 | Partial | Preview attributes supplied where available. Preview card remains client work. |
| MAP-07 | Completed | Approved destinations near a point |
| MAP-08 | Completed | 5, 10, 25 and 50 km filters |
| MAP-09 | Partial | Route geometry and instructions supplied. Drawing remains client work. |
| MAP-10 | Completed | Routed distance and estimated duration |
| MAP-11 | Completed | Driving, walking and cycling |
| MAP-12 | Partial | Stable source IDs and existing detail paths. Navigation and public detail for activity/guide remain pending. |
| MAP-13 | Client-side pending | Marker clustering |
| MAP-14 | Client-side pending | Map/list view toggle |
| MAP-15 | Client-side pending | External Google Maps navigation |
| MAP-SP-01 | Client-side pending | Select a pin on a map |
| MAP-SP-02 | Client-side pending | Device GPS permission and location capture |
| MAP-SP-03 | Partial | Existing listing addresses and rental pickup addresses supported. Tour Guide stores a public base city/district only. |
| MAP-SP-04 | Client-side pending | Adjust the pin before save |
| MAP-SP-05 | Completed | Validated coordinate persistence with ownership checks |
| MAP-SP-06 | Completed | Update locations and trigger source-specific reapproval |
| MAP-AD-01 | Completed | Review supported registered listing and pickup locations |
| MAP-AD-02 | Partial | Admin can inspect addresses and coordinates. Field verification and review UI remain pending. |
| MAP-AD-03 | Completed | Existing listing decisions plus guide and rental pickup decisions |
| MAP-AD-04 | Completed | Authorized, transactional corrections with audit trail |
| MAP-AD-05 | Blocked | Standalone attraction catalog requires schema approval |

## Verification

Live tests on 19 November 2026 used the backend routing service and configured
key, within a 40-second overall deadline. Geoapify returned real geometry and
instructions on all three routes.

| Route | Travel mode | HTTP | Geometry points | Distance | Duration | Instructions |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Colombo Fort to Galle Face | Walking | 200 | 84 | 1,583 m | 1,551.273 s | 16 |
| Jaffna town to Nallur | Cycling | 200 | 115 | 1,871 m | 375.274 s | 4 |
| Kandy city to Peradeniya | Driving | 200 | 208 | 5,942 m | 386.397 s | 9 |

The 90-second bounded focused regression command reported **84 passed** in 13.84 seconds
with three dependency deprecation warnings. This includes 26 Interactive Map cases,
16 Live Weather cases, three existing rate-limiter cases, and 39 Homestay, Artisan,
and Eco-Tourism regressions. Automated route tests mock Geoapify. Catalog and
management tests use isolated in-memory SQLite. The SQL Server geography statement
was compiled offline for the implementation run.

Final pre-commit verification reran the focused set and added the Tour Guide
model regressions. **147 passed** in 18.09 seconds under a 90-second deadline,
with three dependency deprecation warnings. The approved
`CeylonGoDB_IntegrationTest` database on SQL Server 17.0.1000.7 executed the
production bounds, search, review and nearby queries. Controlled rows verified
SRID 4326 distance, invalid-coordinate and radius exclusion, and correct
ordering across pages. A transactional activity fixture exercised nonempty
production pages at 0 and 2.210 km and verified that admin corrections write
the actor, entity, reason and exact coordinate change to `admin_audit_logs`.
The fixture and audit writes were rolled back and checked for absence. No
primary database connection or migration execution took place.

**Approval race resolved.** The original Activity update could use a stale draft
read to write new coordinates after approval had committed. Activity, Homestay,
and Eco-Tourism location edits and approval decisions now compare the observed
location and review state in the same SQL update. A stale write returns HTTP 409.
Eco-Tourism publication and Artisan verification also use conditional writes.
Rental pickup review and Tour Guide location review already check their observed
location snapshot when approving or writing location changes.

The follow-up 90-second focused run reported **155 passed** in 18.65 seconds,
including eight new deterministic interleaving cases, with three dependency
deprecation warnings. Tests cover approval winning before the provider writes,
a stale approval after a location change, Eco-Tourism publication after a
location edit, and Artisan verification after a location change.

A separate probe on `CeylonGoDB_IntegrationTest` (SQL Server 17.0.1000.7) used
a 65-second process deadline and a four-second lock timeout. Both Activity
interleavings returned HTTP 409 through the production handlers. The public
catalog retained the reviewed coordinates when approval won and hid the changed
location until a fresh approval when the edit won. Normal Activity edit,
submission, and approval passed. The Homestay and Eco-Tourism conditional
writes also rejected stale location and approval snapshots on SQL Server.
All four fixtures were rolled back and checked for absence. No primary
database or migration execution was involved.
