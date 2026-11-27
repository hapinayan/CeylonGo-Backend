# Personalized Recommendation API

CeylonGo ranks published Sri Lankan tourism listings for guests and travelers. The Academic MVP uses deterministic content matching with its existing SQL Server catalog, JWT authentication, traveler preferences, wishlist and booking records. It serves Angular and Flutter through the same API and requires no location permission.

## Routes and authentication

Paths below are relative to `/api/v1/recommendations`. Send `Authorization: Bearer <access-token>` on traveler routes and optionally on `/home`. Guests omit the header. An invalid or expired bearer token supplied to `/home` returns 401. A valid non-traveler session gets guest sections on `/home`.

| Method and path | Access | Request | Success |
| --- | --- | --- | --- |
| `GET /popular` | Public | Optional `category`, `district`, `limit` | 200, group |
| `GET /me` | Traveler | Optional `category`, `district`, `recommendation_type`, `limit` | 200, group |
| `GET /home` | Public or traveler | Optional `category`, `district`, `limit` per section | 200, `{"sections":[...]}` |
| `GET /similar/{listing_id}` | Public | Positive listing ID, optional `category`, `district`, `limit` | 200, group |
| `POST /activity` | Traveler | Search or view JSON | 200, activity result |
| `GET /settings` | Traveler | None | 200, consent settings |
| `PATCH /settings` | Traveler | `{"behavioral_personalization":true}` or `false` | 200, updated consent settings |
| `DELETE /history` | Traveler | None | 204, empty body |

Categories are `homestay`, `adventure`, `guide`, `bike` (motorcycles and scooters), `bicycle`, and `artisan` (local business and artisan showcases). `district` accepts a district or locality substring of up to 80 characters, such as `Nallur`. It is case-insensitive. `limit` is 1–30, defaults to 12 for a group, and defaults to 6 per homepage section. These ranked samples have no page cursor.

| Group key | Selection |
| --- | --- |
| `recommended_for_you` | Weighted personal ranking with the general ranking as a cold-start fallback. Default for `/me`. |
| `because_you_viewed` | Related services excluding recently viewed listings. |
| `based_on_your_interests` | Services matching explicitly selected interests. |
| `favorite_destinations` | Services in saved destinations or recently searched places. |
| `discover_something_new` | Other published services, excluding listings already viewed, saved, or booked when that history is enabled. |
| `popular_experiences` | General ranking based on aggregate wishlist saves and recent confirmed or completed bookings. |
| `similar_services` | Related services excluding the source. Available from `/similar/{listing_id}`. |

All keys except `similar_services` are valid for `/me?recommendation_type=…`. A traveler homepage supplies all six `/me` keys. A guest homepage supplies `popular_experiences` and `discover_something_new`. A listing appears once across the homepage. Sections with little evidence or a small catalog may be empty.

## Sample responses

Example request `GET /api/v1/recommendations/popular?category=adventure&district=Badulla&limit=3`. The values below illustrate the contract.

```json
{
  "recommendation_type": "popular_experiences",
  "items": [
    {
      "listing_id": 41,
      "category": "adventure",
      "title": "Ella Ridge Walk",
      "short_description": "A guided half-day walk along the ridge.",
      "district": "Badulla",
      "location": "Ella",
      "cover_image_url": "/api/v1/listings/41/photos/89",
      "starting_price": "2600.00",
      "currency": "LKR",
      "pricing_unit": "per_participant",
      "rating": null,
      "recommendation_score": 100.0,
      "recommendation_reason": "Popular among travelers",
      "detail_route": "/api/v1/activities/41",
      "bookable": true
    }
  ]
}
```

- `listing_id` is the catalog ID. Use `category` and `listing_id` for application navigation. `detail_route` is the public backend path.
- `title`, `short_description`, `district`, and `location` are listing data. Description is capped at 240 characters. `location` is the locality.
- `cover_image_url` is an absolute URL, an API-origin-relative path, or null. Only visible media is used. Resolve paths such as `/api/v1/listings/41/photos/89` against the backend origin.
- `starting_price` is a decimal string or null. `currency` and `pricing_unit` are nullable. Units are `per_night`, `per_day`, `per_participant`, `per_person`, and `per_group`. Guide pricing comes from active packages and uses the displayed package's pricing mode. Artisan showcases may display product prices in their own module.
- `rating` is nullable and currently null because the shared MVP catalog does not expose a common rating source.
- `recommendation_score` is a number in 0–100. `recommendation_reason` is human-readable and limited to evidence used for ranking. Treat it as display text.
- `bookable` indicates that a supported service has priced inventory matching an indicative availability check. Stays and rentals check the next Sri Lanka calendar day. Activities and guides check future slots. Clients must check requested dates, inventory, party size, and current price through the category-specific booking flow. Artisans always have `bookable: false` and use the existing showcase/contact flow.

Example homepage response with empty sections.

```json
{
  "sections": [
    {"recommendation_type": "popular_experiences", "items": []},
    {"recommendation_type": "discover_something_new", "items": []}
  ]
}
```

## Preferences, consent, and activity

Use the existing `GET /api/v1/traveler/preferences` and `PUT /api/v1/traveler/preferences` endpoints for explicit preferences. The existing `preferred_destinations` accepts districts and localities. The same preference API now accepts the interest values `mountains_hiking`, `photography`, and `relaxation`. Explicit preferences can affect recommendations with behavioral personalization disabled.

Consent defaults to false. When false, the recommender skips personal search, view, wishlist, and booking signals. Existing historical events remain subject to expiry, and can be cleared while opted out. Opting in again makes unexpired, uncleared events eligible to influence recommendations. Clearing history removes only this traveler's search and view events. Wishlist and booking signals resume on opt-in because their records are independent. Public popularity uses aggregate platform engagement.

One search event after the user submits a search.

```json
{
  "event_type": "search",
  "search_query": "ridge trek",
  "category": "adventure",
  "district": "Badulla",
  "destination": "Ella",
  "filters": {
    "activity_category": "hiking-trekking",
    "max_price": 4000,
    "pricing_unit": "per_participant"
  }
}
```

Provide at least one populated search attribute. Supported filters are `activity_category` (the activity discovery enum), `rental_subtype` (the rental discovery enum), `min_price`, `max_price`, and `pricing_unit`. Price bounds are whole LKR amounts from 0 through 1,000,000,000, inclusive. Supply a unit and at least one bound together. Search text is limited to 120 characters and locations to 80. Text is normalized, including case and whitespace. The API rejects email, phone, IP-style text, and long token-like words. Send tourism search terms and allowlisted filters only.

One view event after a service detail loads successfully.

```json
{"event_type": "view", "listing_id": 41, "category": "adventure"}
```

`category` is optional on views and must match when provided. The JWT supplies the traveler ID and the server sets the UTC timestamp. Do not send a user ID, timestamp, URL, tracking ID, or credentials in the event body. A ten-minute duplicate window and 20 unique accepted events per traveler per rolling minute apply. Guest activity is never persisted.

```json
{"recorded": true, "reason": "recorded"}
```

A successful no-op returns `recorded: false` and `reason: "duplicate"` or `"personalization_disabled"`.

| Status | Client handling |
| --- | --- |
| 401 | Refresh or clear the session. Retry public homepage without authorization after session expiry. |
| 403 | Traveler authentication is required for the requested operation. |
| 404 | The source or viewed listing is missing or no longer public. |
| 422 | Validate route/query parameters or JSON fields. FastAPI supplies validation details in `detail`. |
| 429 | Stop submitting activity until the `Retry-After` interval in seconds has elapsed. |
| 503 | Required schema is unavailable. Keep ordinary discovery available and hide consent controls until deployment is complete. |

## Architecture and ranking

The router applies JWT and request validation. The service reuses the published-listing query, which checks listing approval and active state, approved provider status, and an active provider user. It selects up to 100 newest IDs per eligible category after database filters. History sources, listing metadata, inventory and media are loaded in batches. Each listing appears once per result. Diversity uses a small reranking penalty for categories and districts already selected. The final tie-break is ascending listing ID.

The main group calculates `100 × sum(weight × signal) / sum(weights of available signals)`. Weights are in `app/services/mvp_recommendations.py`. Each signal is in 0–1.

| Signal | Initial weight | Evidence |
| --- | ---: | --- |
| Explicit preferences | 30% | Interests and travel style against catalog metadata, titles and descriptions. Location, accommodation and transport preferences map to listing fields and categories. |
| Search activity | 25% | Best decayed match across search words, category, place, and applicable filters. |
| Views | 15% | Best decayed similarity to distinct eligible recently viewed listings. |
| Wishlist | 15% | Best similarity to currently saved eligible listings. |
| Bookings | 10% | Best decayed similarity to eligible confirmed or completed bookings. Cancelled, pending and rejected bookings contribute no positive signals. |
| Budget | 5% | One minimum bookable order compared with the saved LKR trip ceiling. Activities account for minimum participants. Unknown prices and absent ceilings omit this signal. |

Searches, views and booking evidence have a 30-day half-life. Matching uses the strongest individual behavioral evidence per signal, so repeated events cannot accumulate scores. Similarity uses category, shared topics and location. The most influential matching signal provides the card reason. General popularity counts wishlist saves once and weights a recent eligible booking twice. It is used for general ranking and cold start. If the catalog has no engagement, published services are still discoverable with a score of zero.

## Angular integration

Use the existing HTTP and JWT services. Load `/home` once per homepage refresh, bind ordered sections to the service-card component, and hide empty groups. Resolve image URLs against the backend origin. Use the app's category route map rather than treating `detail_route` as an Angular path. Read consent at sign-in or when opening preferences. Send a search event after submission and a view after successful detail load only when enabled. Keep activity failures independent of discovery and booking.

## Flutter integration

Use the shared API client and auth interceptor. Decode `sections` as an ordered list and match `recommendation_type` to localized headings. Decode IDs as integers, scores as `num`, prices as nullable strings, and optional media as nullable URIs resolved against the backend origin. Use `category` with `listing_id` for navigation. Refresh the homepage on preference changes, opt-out, or successful history deletion, and discard cached personalized content on logout.

## SQL Server deployment and retention

The additive V1.0.9 migration adds `traveler_activity_events` with indexed owner/time, deduplication and expiry access, and a default-off `traveler_settings.behavioral_personalization` column. It uses the existing database and adds one table. On the supplied 31-table V1.0.8 MVP baseline, the expected count is 32. The independent numbered migration stream is outside this MVP upgrade.

1. Confirm the server, database, and V1.0.8 ledger. Use the MVP runner to check the plan.

   ```powershell
   python scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --target 1.0.9 --dry-run
   ```

2. Have the coordinator take a SQL Server full copy-only backup with checksum to a new secured backup file before approval to apply the change. Run `RESTORE VERIFYONLY ... WITH CHECKSUM` against that file and record the result, backup location, and restore owner. Use the approved SQL Server backup and recovery process if restoration is required.
3. After explicit coordinator approval, apply the checksum-pinned upgrade with the existing migration lock and transaction.

   ```powershell
   python scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --target 1.0.9 --yes
   python scripts/bootstrap_mvp_schema.py CeylonGoDB status
   ```

4. Verify the ledger, the table and three indexes, and the consent default. Configure the existing host scheduler to run this command at least hourly against the approved MVP database.

   ```powershell
   python -m scripts.purge_mvp_recommendation_activity
   ```

   The ranking cutoff is 90 days. The hourly cleanup deletes only events older than 90 days, so physical deletion may lag by up to one hour during normal operation. Monitor the exit status and alert on missed runs, because physical expiry depends on execution of this job. The command logs a count or a generic failure and checks the selected database before writes. Limit backup access and account for backups separately in the deployment's retention policy.

## Known limitations

Catalog selection is bounded per category. Keyword and topic matching support straightforward tourism phrases. It does not translate queries or infer destinations from GPS. Budget scoring estimates affordability of a minimum order and makes no trip-total prediction. Availability is indicative, and a stay with availability split across several shorter slots can be omitted from the bookable indicator. Mobile and web clients must send the activity events. SQL Server migration and end-to-end client acceptance require deployment verification.
